import os
import sqlite3
import random
import string
import logging
import time
import subprocess
import json
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ===== SETUP =====
TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))

if not TOKEN or not ADMIN_ID:
    print("❌ Missing BOT_TOKEN or ADMIN_ID")
    exit(1)

logging.basicConfig(level=logging.INFO)
print("✅ Bot starting...")

# ===== DATABASE =====
DB = 'bot.db'

def get_db():
    conn = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init():
    conn = get_db()
    c = conn.cursor()
    
    # ✅ NO DROP TABLE - DATA IS NEVER DELETED!
    # Only create tables if they don't exist
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        balance REAL DEFAULT 0,
        referrals INTEGER DEFAULT 0,
        referred_by INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS codes (
        code TEXT PRIMARY KEY,
        amount REAL,
        used INTEGER DEFAULT 0,
        created_by INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS hosting_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        domain TEXT,
        username TEXT,
        password TEXT,
        ip_address TEXT,
        plan TEXT,
        duration TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        expiry_date TEXT
    )''')
    
    conn.commit()
    conn.close()
    print("✅ Database ready (data preserved!)")

init()

# ===== RAW VPS FUNCTION =====

def create_raw_vps(username, password):
    """Create VPS using RAW CLI"""
    try:
        print(f"🚀 Creating VPS for {username}...")
        
        # Check if raw is installed
        check_raw = subprocess.run(['which', 'raw'], capture_output=True, text=True)
        if not check_raw.stdout.strip():
            return {'success': False, 'error': 'RAW CLI not installed. Run in Console: npm install -g rawhq'}
        
        # Deploy VPS
        cmd = f"raw deploy --type raw-free --region eu --name {username}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        
        if result.returncode != 0:
            error = result.stderr or result.stdout
            if "already exists" in error.lower():
                username = f"{username}_{random.randint(10,99)}"
                cmd = f"raw deploy --type raw-free --region eu --name {username}"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
                if result.returncode != 0:
                    return {'success': False, 'error': result.stderr or result.stdout}
            elif "not authenticated" in error.lower() or "invalid" in error.lower():
                return {'success': False, 'error': 'RAW not authenticated. Run in Console: raw init'}
            else:
                return {'success': False, 'error': error}
        
        time.sleep(10)
        
        # Get IP
        ip_cmd = "raw status --output json"
        ip_result = subprocess.run(ip_cmd, shell=True, capture_output=True, text=True, timeout=30)
        
        ip = "IP will be available soon"
        if ip_result.returncode == 0 and ip_result.stdout:
            try:
                data = json.loads(ip_result.stdout)
                for server in data.get('servers', []):
                    if server.get('name') == username:
                        ip = server.get('ip', 'IP will be available soon')
                        break
            except:
                pass
        
        return {
            'success': True,
            'ip': ip,
            'username': 'root',
            'password': password,
            'domain': f"{username}.raw-host.com"
        }
        
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': 'Deployment timed out'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def delete_raw_vps(username):
    try:
        cmd = f"raw destroy {username}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        return result.returncode == 0
    except:
        return False

# ===== DATABASE FUNCTIONS =====

def get_user(uid):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE user_id = ?', (uid,))
        r = c.fetchone()
        conn.close()
        if r:
            return dict(r)
        return None
    except:
        return None

def user_exists(uid):
    return get_user(uid) is not None

def add_user(uid, username, first_name, ref=0):
    try:
        conn = get_db()
        c = conn.cursor()
        
        if user_exists(uid):
            conn.close()
            return False
        
        c.execute('INSERT INTO users (user_id, username, first_name, referred_by) VALUES (?, ?, ?, ?)',
                  (uid, username, first_name, ref))
        
        if ref and ref != uid:
            c.execute('UPDATE users SET balance = balance + 15, referrals = referrals + 1 WHERE user_id = ?', (ref,))
            c.execute('SELECT referrals FROM users WHERE user_id = ?', (ref,))
            count = c.fetchone()[0]
            if count % 5 == 0:
                c.execute('UPDATE users SET balance = balance + 25 WHERE user_id = ?', (ref,))
        
        conn.commit()
        conn.close()
        return True
    except:
        return False

def update_balance(uid, amt):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (float(amt), uid))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def get_refs(uid):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM users WHERE referred_by = ?', (uid,))
        r = c.fetchone()[0]
        conn.close()
        return r
    except:
        return 0

def gen_code(amt, created_by):
    try:
        conn = get_db()
        c = conn.cursor()
        parts = []
        for i in range(4):
            part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
            parts.append(part)
        code = f"DYNO-{parts[0]}-{parts[1]}-{parts[2]}"
        c.execute('INSERT INTO codes (code, amount, created_by) VALUES (?, ?, ?)', (code, float(amt), created_by))
        conn.commit()
        conn.close()
        return code
    except:
        return None

def use_code(code, uid):
    try:
        conn = get_db()
        c = conn.cursor()
        
        c.execute('SELECT * FROM codes WHERE code = ? AND used = 0', (code,))
        r = c.fetchone()
        
        if not r:
            conn.close()
            return False, 0
        
        amount = float(r[1])
        
        c.execute('UPDATE codes SET used = 1 WHERE code = ?', (code,))
        c.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, uid))
        
        conn.commit()
        conn.close()
        return True, amount
    except:
        return False, 0

def save_hosting_account(user_id, domain, username, password, ip, plan, duration):
    try:
        conn = get_db()
        c = conn.cursor()
        expiry = (datetime.now() + timedelta(hours=duration)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute('''INSERT INTO hosting_accounts (user_id, domain, username, password, ip_address, plan, duration, expiry_date) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, domain, username, password, ip, plan, f"{duration} hours", expiry))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def get_hosting_account(user_id):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM hosting_accounts WHERE user_id = ? AND status = "active"', (user_id,))
        r = c.fetchone()
        conn.close()
        if r:
            return dict(r)
        return None
    except:
        return None

def get_expired_servers():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM hosting_accounts WHERE status = "active" AND expiry_date <= datetime("now")')
        r = c.fetchall()
        conn.close()
        return r
    except:
        return []

def deactivate_server(server_id):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('UPDATE hosting_accounts SET status = "expired" WHERE id = ?', (server_id,))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def get_total():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM users')
        r = c.fetchone()[0]
        conn.close()
        return r
    except:
        return 0

def get_top_users(limit=10):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT user_id, username, referrals, balance FROM users ORDER BY referrals DESC LIMIT ?', (limit,))
        r = c.fetchall()
        conn.close()
        return r
    except:
        return []

def get_total_balance():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT SUM(balance) FROM users')
        r = c.fetchone()[0] or 0
        conn.close()
        return r
    except:
        return 0

def get_unused_codes():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM codes WHERE used = 0')
        r = c.fetchone()[0]
        conn.close()
        return r
    except:
        return 0

# ===== PLANS =====
PLANS = {
    '5hours': {'name': '⏰ 5 Hours', 'price': 10, 'cpu': '1 vCPU', 'ram': '1 GB', 'storage': '10 GB', 'duration': 5},
    '1day': {'name': '📅 1 Day', 'price': 25, 'cpu': '1 vCPU', 'ram': '1 GB', 'storage': '10 GB', 'duration': 24},
    '2days': {'name': '📅 2 Days', 'price': 45, 'cpu': '2 vCPU', 'ram': '2 GB', 'storage': '20 GB', 'duration': 48},
    '3days': {'name': '📅 3 Days', 'price': 60, 'cpu': '2 vCPU', 'ram': '2 GB', 'storage': '20 GB', 'duration': 72},
    '1week': {'name': '📅 1 Week', 'price': 100, 'cpu': '2 vCPU', 'ram': '4 GB', 'storage': '40 GB', 'duration': 168}
}

# ===== MAIN MENU =====
async def show_main_menu(update, context):
    try:
        if hasattr(update, 'message') and update.message:
            uid = update.message.from_user.id
            is_message = True
        else:
            query = update.callback_query
            uid = query.from_user.id
            await query.answer()
            is_message = False
        
        user = get_user(uid)
        if user:
            balance = float(user['balance']) if user['balance'] else 0
            username = user['username'] or "User"
        else:
            balance = 0
            username = "User"
        
        refs = get_refs(uid)
        hosting = get_hosting_account(uid)
        hosting_status = "❌ No Active VPS" if not hosting else f"✅ Active: {hosting['domain']}"
        
        keyboard = [
            [InlineKeyboardButton("🛒 BUY VPS", callback_data='plans')],
            [InlineKeyboardButton("👤 PROFILE", callback_data='profile'), InlineKeyboardButton("🎁 REDEEM", callback_data='redeem')],
            [InlineKeyboardButton("👥 REFERRAL", callback_data='referral'), InlineKeyboardButton("📊 MY VPS", callback_data='my_hosting')]
        ]
        
        text = f"""📊 USER DASHBOARD

━━━━━━━━━━━━━━━━━━━━━
✨ Welcome, @{username} 🎉
━━━━━━━━━━━━━━━━━━━━━

🆔 User ID: {uid}
💰 Balance: {balance:.2f} Credits
👥 Total Invites: {refs} users
📊 VPS Status: {hosting_status}

━━━━━━━━━━━━━━━━━━━━━
🔗 Your Invite Link:
https://t.me/{context.bot.username}?start={uid}
━━━━━━━━━━━━━━━━━━━━━

Invite friends to earn credits!"""
        
        if is_message:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"❌ Error in main menu: {e}")

# ===== START =====
async def start(update, context):
    try:
        uid = update.effective_user.id
        username = update.effective_user.username or ""
        first_name = update.effective_user.first_name or "User"
        ref = context.args[0] if context.args else 0
        
        if ref and int(ref) == uid:
            ref = 0
        
        # ✅ CHECK IF USER EXISTS - DON'T OVERWRITE
        if user_exists(uid):
            await show_main_menu(update, context)
            return
        
        add_user(uid, username, first_name, int(ref) if ref else 0)
        
        if ref:
            try:
                referrer = get_user(int(ref))
                if referrer:
                    balance = referrer['balance']
                    await context.bot.send_message(
                        int(ref),
                        f"🎉 New Referral!\n\n@{username} joined!\n✅ +15 Credits!\n💰 New Balance: {balance:.2f} Credits"
                    )
            except:
                pass
        
        await show_main_menu(update, context)
    except Exception as e:
        print(f"❌ Error in start: {e}")
        await update.message.reply_text("⚠️ Error! Try again.")

# ===== MENU =====
async def menu(update, context):
    await show_main_menu(update, context)

# ===== BACK =====
async def back(update, context):
    query = update.callback_query
    await query.answer()
    await show_main_menu(update, context)

# ===== MY VPS =====
async def my_hosting(update, context):
    query = update.callback_query
    await query.answer()
    
    uid = query.from_user.id
    hosting = get_hosting_account(uid)
    keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data='back')]]
    
    if not hosting:
        text = """📊 MY VPS

━━━━━━━━━━━━━━━━━━━━━

❌ No Active VPS Found!

Buy a VPS plan from the PLANS menu.
Earn credits by referring friends!"""
    else:
        expiry = datetime.strptime(hosting['expiry_date'], '%Y-%m-%d %H:%M:%S')
        remaining = expiry - datetime.now()
        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)
        
        text = f"""📊 MY VPS

━━━━━━━━━━━━━━━━━━━━━

🌐 Domain: {hosting['domain']}
🖥️ IP: {hosting['ip_address']}
👤 Username: {hosting['username']}
🔑 Password: {hosting['password']}
📦 Plan: {hosting['plan']}
⏳ Remaining: {hours}h {minutes}m
📅 Expires: {hosting['expiry_date']}

━━━━━━━━━━━━━━━━━━━━━

SSH: ssh {hosting['username']}@{hosting['ip_address']}

⚠️ Save your credentials!"""
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ===== PLANS =====
async def show_plans(update, context):
    try:
        query = update.callback_query
        await query.answer()
        
        text = "🛒 VPS PLANS\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        keyboard = []
        for k, v in PLANS.items():
            text += f"{v['name']}\n💰 Price: {v['price']} Credits\n⚡ CPU: {v['cpu']}\n💾 RAM: {v['ram']}\n⏳ Duration: {v['duration']} hours\n\n"
            keyboard.append([InlineKeyboardButton(f"🛒 {v['name']} - {v['price']} Credits", callback_data=f'buy_{k}')])
        keyboard.append([InlineKeyboardButton("🔙 BACK", callback_data='back')])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"❌ Error in plans: {e}")

# ===== BUY PLAN =====
async def buy_plan(update, context):
    try:
        query = update.callback_query
        await query.answer()
        
        uid = query.from_user.id
        key = query.data.split('_')[1]
        plan = PLANS[key]
        user = get_user(uid)
        
        if not user:
            await query.edit_message_text("❌ Please /start first!")
            return
        
        balance = float(user['balance']) if user['balance'] else 0
        
        if balance < plan['price']:
            await query.edit_message_text(
                f"❌ Insufficient Balance!\n\nNeed: {plan['price']} Credits\nHave: {balance:.2f} Credits\n\nEarn more via referrals or redeem codes!"
            )
            return
        
        update_balance(uid, -plan['price'])
        
        username = f"user{uid}_{random.randint(100,999)}"
        password = ''.join(random.choices(string.ascii_letters + string.digits + "!@#$%^&*", k=12))
        
        await query.edit_message_text(
            f"⏳ Creating your VPS ({plan['duration']} hours)...\n\nPlease wait, this may take a few minutes."
        )
        
        result = create_raw_vps(username, password)
        
        if result['success']:
            save_hosting_account(
                uid, 
                result.get('domain', f"{username}.raw-host.com"),
                result['username'],
                password,
                result['ip'],
                plan['name'],
                plan['duration']
            )
            
            creds_text = f"""✅ VPS ACTIVATED! 🎉

━━━━━━━━━━━━━━━━━━━━━
🌐 Domain: {result.get('domain', f"{username}.raw-host.com")}
🖥️ IP: {result['ip']}
👤 Username: {result['username']}
🔑 Password: {password}
📦 Plan: {plan['name']}
⏳ Duration: {plan['duration']} hours
━━━━━━━━━━━━━━━━━━━━━

SSH: ssh {result['username']}@{result['ip']}

⚠️ Save your credentials!"""
            
            await query.edit_message_text(creds_text)
            
            await context.bot.send_message(
                ADMIN_ID,
                f"✅ NEW VPS!\n\nUser: {uid}\nPlan: {plan['name']}\nIP: {result['ip']}"
            )
        else:
            update_balance(uid, plan['price'])
            
            await query.edit_message_text(
                f"❌ VPS Creation Failed!\n\nError: {result.get('error', 'Unknown error')}\n\n💰 Credits have been refunded.\n\nContact admin: @Free_hostingbyreferbot"
            )
    except Exception as e:
        print(f"❌ Error in buy: {e}")
        await query.edit_message_text("❌ Error! Try again.")

# ===== PROFILE =====
async def profile(update, context):
    try:
        query = update.callback_query
        await query.answer()
        
        uid = query.from_user.id
        user = get_user(uid)
        if not user:
            await query.edit_message_text("❌ /start first!")
            return
        
        refs = get_refs(uid)
        balance = float(user['balance']) if user['balance'] else 0
        hosting = get_hosting_account(uid)
        
        text = f"""👤 PROFILE

🆔 ID: {uid}
💰 Balance: {balance:.2f} Credits
👥 Referrals: {refs}
💻 VPS: {'✅ Active' if hosting else '❌ None'}"""
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 BACK", callback_data='back')]]))
    except Exception as e:
        print(f"❌ Error in profile: {e}")

# ===== REDEEM =====
async def redeem(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🎁 REDEEM\n\nSend: /redeem DYNO-XXXX-XXXX-XXXX",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 BACK", callback_data='back')]])
    )

async def redeem_command(update, context):
    try:
        if not context.args:
            await update.message.reply_text("❌ Usage: /redeem DYNO-XXXX-XXXX-XXXX")
            return
        
        code = context.args[0].upper()
        uid = update.effective_user.id
        
        success, amount = use_code(code, uid)
        
        if success:
            user = get_user(uid)
            await update.message.reply_text(
                f"✅ Redeemed!\n\n💰 +{amount} Credits\n💳 Balance: {user['balance']:.2f} Credits"
            )
        else:
            await update.message.reply_text("❌ Invalid code!")
    except Exception as e:
        print(f"❌ Error in redeem: {e}")
        await update.message.reply_text("❌ Error!")

# ===== REFERRAL =====
async def referral(update, context):
    query = update.callback_query
    await query.answer()
    
    uid = query.from_user.id
    link = f"https://t.me/{context.bot.username}?start={uid}"
    refs = get_refs(uid)
    
    await query.edit_message_text(
        f"👥 REFERRAL\n\n🔗 {link}\n\n📊 Referrals: {refs}\n\n🎁 15 Credits each\n🎁 25 Bonus every 5 referrals",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 BACK", callback_data='back')]])
    )

# ===== SUPPORT =====
async def support(update, context):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("🔙 BACK", callback_data='back')]]
    text = """📞 SUPPORT

❓ How to earn?
1️⃣ Refer friends → 15 credits
2️⃣ Every 5 referrals → 25 bonus
3️⃣ Redeem codes

❓ How to get VPS?
1️⃣ Earn credits
2️⃣ Buy a plan
3️⃣ VPS created automatically!

⏳ Plans:
• 5 Hours - 10 Credits
• 1 Day - 25 Credits
• 2 Days - 45 Credits
• 3 Days - 60 Credits
• 1 Week - 100 Credits"""
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ===== ADMIN =====
async def admin_panel(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!")
        return
    
    total = get_total()
    keyboard = [
        [InlineKeyboardButton("🔑 GENERATE CODE", callback_data='gen_code')],
        [InlineKeyboardButton("📊 STATS", callback_data='stats')]
    ]
    await update.message.reply_text(f"🛠️ ADMIN\n\n👥 Users: {total}", reply_markup=InlineKeyboardMarkup(keyboard))

async def gen_code_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /gencode AMOUNT")
        return
    amount = float(context.args[0])
    code = gen_code(amount, ADMIN_ID)
    await update.message.reply_text(f"✅ Code: {code}\nAmount: {amount} Credits")

async def gencode_callback(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Send: /gencode AMOUNT")

async def stats_callback(update, context):
    query = update.callback_query
    await query.answer()
    total = get_total()
    total_bal = get_total_balance()
    unused = get_unused_codes()
    await query.edit_message_text(
        f"📊 STATS\n\n👥 Users: {total}\n💰 Balance: {total_bal:.2f}\n🎁 Unused Codes: {unused}"
    )

# ===== AUTO-EXPIRY =====
async def check_expired_servers(context):
    try:
        expired = get_expired_servers()
        for server in expired:
            delete_raw_vps(server['username'])
            deactivate_server(server['id'])
            print(f"🗑️ Deleted expired VPS: {server['domain']}")
    except Exception as e:
        print(f"❌ Error checking expired servers: {e}")

# ===== MAIN =====
def main():
    print("🚀 Starting bot...")
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("redeem", redeem_command))
    app.add_handler(CommandHandler("gencode", gen_code_cmd))
    app.add_handler(CommandHandler("admin", admin_panel))
    
    app.add_handler(CallbackQueryHandler(show_plans, pattern='^plans$'))
    app.add_handler(CallbackQueryHandler(profile, pattern='^profile$'))
    app.add_handler(CallbackQueryHandler(redeem, pattern='^redeem$'))
    app.add_handler(CallbackQueryHandler(referral, pattern='^referral$'))
    app.add_handler(CallbackQueryHandler(my_hosting, pattern='^my_hosting$'))
    app.add_handler(CallbackQueryHandler(support, pattern='^support$'))
    app.add_handler(CallbackQueryHandler(back, pattern='^back$'))
    app.add_handler(CallbackQueryHandler(buy_plan, pattern='^buy_'))
    app.add_handler(CallbackQueryHandler(gencode_callback, pattern='^gen_code$'))
    app.add_handler(CallbackQueryHandler(stats_callback, pattern='^stats$'))
    
    # Auto-expiry checker
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(check_expired_servers, interval=300, first=30)
        print("✅ Auto-expiry checker started")
    
    print("🤖 Bot is running!")
    app.run_polling()

if __name__ == "__main__":
    main()