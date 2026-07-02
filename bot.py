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
    
    c.execute('DROP TABLE IF EXISTS users')
    c.execute('DROP TABLE IF EXISTS codes')
    c.execute('DROP TABLE IF EXISTS hosting_accounts')
    
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
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        expiry_date TEXT
    )''')
    conn.commit()
    conn.close()
    print("✅ Database initialized!")

init()

# ===== RAW VPS FUNCTIONS (NO API KEY NEEDED) =====

def check_raw_installed():
    """Check if RAW CLI is installed"""
    try:
        subprocess.run(['raw', '--version'], capture_output=True, check=True, timeout=10)
        return True
    except:
        return False

def create_raw_vps(username, password):
    """
    Create a VPS using RAW CLI - NO API KEY NEEDED
    RAW handles authentication internally after 'raw init'
    """
    try:
        # Check if RAW is installed
        if not check_raw_installed():
            return {'success': False, 'error': 'RAW CLI not installed. Run: npm install -g rawhq'}
        
        print(f"🚀 Deploying RAW VPS for {username}...")
        
        # Deploy the server - simple command, no API key needed
        deploy_cmd = [
            'raw', 'deploy',
            '--type', 'raw-free',
            '--region', 'eu',
            '--name', username
        ]
        
        result = subprocess.run(deploy_cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or 'Deployment failed'
            return {'success': False, 'error': error_msg}
        
        # Wait for server to be ready
        time.sleep(15)
        
        # Try to get server IP
        ip = None
        domain = f"{username}.raw-host.com"
        
        # Method 1: Try JSON output
        try:
            status_cmd = ['raw', 'status', '--output', 'json']
            status_result = subprocess.run(status_cmd, capture_output=True, text=True, timeout=30)
            
            if status_result.returncode == 0 and status_result.stdout:
                data = json.loads(status_result.stdout)
                for server in data.get('servers', []):
                    if server.get('name') == username:
                        ip = server.get('ip', 'unknown')
                        break
        except:
            pass
        
        # Method 2: Try to get IP via SSH
        if not ip or ip == 'unknown':
            try:
                ip_cmd = ['raw', 'ssh', username, '--command', 'curl -s ifconfig.me']
                ip_result = subprocess.run(ip_cmd, capture_output=True, text=True, timeout=30)
                if ip_result.returncode == 0 and ip_result.stdout.strip():
                    ip = ip_result.stdout.strip()
            except:
                pass
        
        # Method 3: Try to find IP in deploy output
        if not ip or ip == 'unknown':
            ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', result.stdout)
            if ip_match:
                ip = ip_match.group(1)
        
        if not ip or ip == 'unknown':
            ip = 'IP will be available soon'
        
        return {
            'success': True,
            'ip': ip,
            'username': 'root',
            'password': password,
            'domain': domain
        }
        
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': 'Deployment timed out'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

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

def add_user(uid, username, first_name, ref=0):
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        c.execute('SELECT * FROM users WHERE user_id = ?', (uid,))
        existing = c.fetchone()
        
        if existing:
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
        if conn:
            conn.close()
        return False

def update_balance(uid, amt):
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (float(amt), uid))
        conn.commit()
        conn.close()
        return True
    except:
        if conn:
            conn.close()
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
    conn = None
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
        if conn:
            conn.close()
        return False, 0

def save_hosting_account(user_id, domain, username, password, ip, plan):
    try:
        conn = get_db()
        c = conn.cursor()
        expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute('''INSERT INTO hosting_accounts (user_id, domain, username, password, ip_address, plan, expiry_date) 
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, domain, username, password, ip, plan, expiry))
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
    'starter': {'name': '🌱 Starter', 'price': 50, 'cpu': '1 vCPU', 'ram': '1 GB', 'storage': '10 GB'},
    'pro': {'name': '🚀 Pro', 'price': 100, 'cpu': '2 vCPU', 'ram': '2 GB', 'storage': '20 GB'},
    'business': {'name': '💼 Business', 'price': 200, 'cpu': '4 vCPU', 'ram': '4 GB', 'storage': '40 GB'},
    'enterprise': {'name': '🏢 Enterprise', 'price': 500, 'cpu': '8 vCPU', 'ram': '8 GB', 'storage': '80 GB'}
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
            [InlineKeyboardButton("👥 REFERRAL", callback_data='referral'), InlineKeyboardButton("🏆 LEADERBOARD", callback_data='leaderboard')],
            [InlineKeyboardButton("📊 MY VPS", callback_data='my_hosting'), InlineKeyboardButton("📞 SUPPORT", callback_data='support')]
        ]
        
        text = f"""📊 USER DASHBOARD

━━━━━━━━━━━━━━━━━━━━━
✨ Welcome, @{username} 🎉
━━━━━━━━━━━━━━━━━━━━━

🆔 User ID: `{uid}`
💰 Balance: `{balance:.2f}` Credits
👥 Total Invites: `{refs}` users
📊 VPS Status: {hosting_status}

━━━━━━━━━━━━━━━━━━━━━
🔗 Your Invite Link:
`https://t.me/{context.bot.username}?start={uid}`
━━━━━━━━━━━━━━━━━━━━━

Invite friends to earn credits!"""
        
        if is_message:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
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
            await update.message.reply_text("⚠️ You cannot refer yourself!")
        
        existing = get_user(uid)
        if existing:
            await show_main_menu(update, context)
            return
        
        add_user(uid, username, first_name, int(ref) if ref else 0)
        
        if ref:
            try:
                referrer = get_user(int(ref))
                if referrer:
                    balance = float(referrer['balance']) if referrer['balance'] else 0
                    await context.bot.send_message(
                        int(ref),
                        f"🎉 New Referral!\n\n@{username} joined using your link!\n✅ +15 Credits!\n💰 New Balance: `{balance:.2f}` Credits",
                        parse_mode='Markdown'
                    )
            except:
                pass
        
        await show_main_menu(update, context)
    except Exception as e:
        print(f"❌ Error in start: {e}")
        await update.message.reply_text("⚠️ An error occurred. Please try again.")

# ===== MENU =====
async def menu(update, context):
    await show_main_menu(update, context)

# ===== BACK =====
async def back(update, context):
    await show_main_menu(update, context)

# ===== MY VPS =====
async def my_hosting(update, context):
    query = update.callback_query
    await query.answer()
    
    uid = query.from_user.id
    hosting = get_hosting_account(uid)
    keyboard = [[InlineKeyboardButton("🔙 BACK TO MENU", callback_data='back')]]
    
    if not hosting:
        text = """📊 MY VPS

━━━━━━━━━━━━━━━━━━━━━

❌ No Active VPS Found!

Buy a VPS plan from the PLANS menu.
Earn credits by referring friends!"""
    else:
        text = f"""📊 MY VPS

━━━━━━━━━━━━━━━━━━━━━

🌐 Domain: `{hosting['domain']}`
🖥️ IP Address: `{hosting['ip_address']}`
👤 Username: `{hosting['username']}`
🔑 Password: `{hosting['password']}`
📦 Plan: {hosting['plan']}
📅 Created: {hosting['created_at']}
📅 Expires: {hosting['expiry_date']}

━━━━━━━━━━━━━━━━━━━━━

SSH Login:
`ssh {hosting['username']}@{hosting['ip_address']}`

⚠️ Save your credentials! You won't see them again!"""
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ===== PLANS =====
async def show_plans(update, context):
    try:
        query = update.callback_query
        await query.answer()
        
        text = "🛒 VPS PLANS\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        keyboard = []
        for k, v in PLANS.items():
            text += f"{v['name']}\n💰 Price: `{v['price']}` Credits\n⚡ CPU: {v['cpu']}\n💾 RAM: {v['ram']}\n📀 Storage: {v['storage']}\n📅 Duration: 30 Days\n\n"
            keyboard.append([InlineKeyboardButton(f"🛒 Buy {v['name']} - {v['price']} Credits", callback_data=f'buy_{k}')])
        keyboard.append([InlineKeyboardButton("🔙 BACK TO MENU", callback_data='back')])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
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
                f"❌ Insufficient Balance!\n\nNeed: `{plan['price']}` Credits\nHave: `{balance:.2f}` Credits\n\nEarn more via referrals or redeem codes!",
                parse_mode='Markdown'
            )
            return
        
        # Deduct credits
        update_balance(uid, -plan['price'])
        
        # Generate credentials
        username = f"user{uid}_{random.randint(100,999)}"
        password = ''.join(random.choices(string.ascii_letters + string.digits + "!@#$%^&*", k=12))
        
        # Show processing message
        await query.edit_message_text(
            "⏳ Creating your VPS...\n\nPlease wait, this may take a few minutes.",
            parse_mode='Markdown'
        )
        
        # Create VPS on RAW
        result = create_raw_vps(username, password)
        
        if result['success']:
            # Save to database
            save_hosting_account(
                uid, 
                result.get('domain', f"{username}.raw-host.com"),
                result['username'],
                password,
                result['ip'],
                plan['name']
            )
            
            # Send credentials
            creds_text = f"""✅ VPS ACTIVATED! 🎉

━━━━━━━━━━━━━━━━━━━━━
🌐 Domain: `{result.get('domain', f"{username}.raw-host.com")}`
🖥️ IP Address: `{result['ip']}`
👤 Username: `{result['username']}`
🔑 Password: `{password}`
📦 Plan: {plan['name']}
━━━━━━━━━━━━━━━━━━━━━

SSH Login:
`ssh {result['username']}@{result['ip']}`

⚠️ Save these credentials! You won't see them again!"""
            
            await query.edit_message_text(creds_text, parse_mode='Markdown')
            
            # Notify admin
            await context.bot.send_message(
                ADMIN_ID,
                f"✅ NEW VPS ACTIVATED!\n\n👤 User: `{uid}`\n📦 Plan: {plan['name']}\n🖥️ IP: `{result['ip']}`\n👤 Username: `{result['username']}`\n🔑 Password: `{password}`",
                parse_mode='Markdown'
            )
        else:
            # Refund if failed
            update_balance(uid, plan['price'])
            
            await query.edit_message_text(
                f"❌ VPS Creation Failed!\n\nError: {result.get('error', 'Unknown error')}\n\n💰 Credits have been refunded.\n\nContact admin: @Free_hostingbyreferbot",
                parse_mode='Markdown'
            )
    except Exception as e:
        print(f"❌ Error in buy: {e}")
        await query.edit_message_text("❌ Error processing your request. Please try again.")

# ===== PROFILE =====
async def profile(update, context):
    try:
        query = update.callback_query
        await query.answer()
        
        uid = query.from_user.id
        user = get_user(uid)
        if not user:
            await query.edit_message_text("❌ Please /start first!")
            return
        
        refs = get_refs(uid)
        balance = float(user['balance']) if user['balance'] else 0
        hosting = get_hosting_account(uid)
        keyboard = [[InlineKeyboardButton("🔙 BACK TO MENU", callback_data='back')]]
        
        text = f"""👤 USER PROFILE
━━━━━━━━━━━━━━━━━━━━━

🆔 User ID: `{uid}`
📛 Username: @{user['username'] or 'N/A'}
💰 Balance: `{balance:.2f}` Credits
👥 Total Referrals: `{refs}`

📊 Referral Progress: {refs}/5 for bonus!
💻 VPS: {'✅ Active' if hosting else '❌ None'}"""
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        print(f"❌ Error in profile: {e}")

# ===== REDEEM =====
async def redeem(update, context):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("🔙 BACK TO MENU", callback_data='back')]]
    await query.edit_message_text(
        "🎁 REDEEM CODE\n━━━━━━━━━━━━━━━━━━━━━\n\nSend the code using:\n`/redeem DYNO-XXXX-XXXX-XXXX`\n\nCodes are provided by admins during promotions!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def redeem_command(update, context):
    try:
        if not context.args:
            await update.message.reply_text(
                "❌ Usage: `/redeem DYNO-XXXX-XXXX-XXXX`\n\nExample: `/redeem DYNO-X5C6-B3TB-CNB0`",
                parse_mode='Markdown'
            )
            return
        
        code = context.args[0].upper()
        uid = update.effective_user.id
        
        user = get_user(uid)
        if not user:
            await update.message.reply_text("❌ Please /start first!")
            return
        
        success, amount = use_code(code, uid)
        
        if success:
            user = get_user(uid)
            balance = float(user['balance']) if user['balance'] else 0
            await update.message.reply_text(
                f"✅ Redeem Successful!\n━━━━━━━━━━━━━━━━━━━━━\n\n💰 +`{amount}` Credits Added!\n💳 New Balance: `{balance:.2f}` Credits\n\nBuy VPS from the PLANS menu!",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "❌ Invalid Code!\n\n• Code may be expired\n• Code may already be used\n• Please check and try again",
                parse_mode='Markdown'
            )
    except Exception as e:
        print(f"❌ Error in redeem: {e}")
        await update.message.reply_text("❌ Error! Please try again.")

# ===== REFERRAL =====
async def referral(update, context):
    try:
        query = update.callback_query
        await query.answer()
        
        uid = query.from_user.id
        link = f"https://t.me/{context.bot.username}?start={uid}"
        refs = get_refs(uid)
        keyboard = [[InlineKeyboardButton("🔙 BACK TO MENU", callback_data='back')]]
        
        text = f"""👥 REFERRAL PROGRAM
━━━━━━━━━━━━━━━━━━━━━

🔗 Your Invite Link:
`{link}`

📊 Your Referrals: `{refs}`

🎁 Rewards System:
• `15` Credits per referral
• `25` Bonus Credits every 5 referrals
• Top referrers get exclusive rewards!

Share your link and earn free VPS hosting!"""
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        print(f"❌ Error in referral: {e}")

# ===== LEADERBOARD =====
async def leaderboard(update, context):
    try:
        query = update.callback_query
        await query.answer()
        
        top = get_top_users(10)
        text = "🏆 TOP REFERRERS\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        if not top:
            text += "No users yet! Be the first! 🚀"
        else:
            for i, u in enumerate(top, 1):
                medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."
                name = f"@{u[1]}" if u[1] else f"User {u[0]}"
                text += f"{medal} {name}\n   👥 {u[2]} referrals | 💰 {u[3]:.0f} credits\n\n"
        
        keyboard = [[InlineKeyboardButton("🔙 BACK TO MENU", callback_data='back')]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        print(f"❌ Error in leaderboard: {e}")

# ===== SUPPORT =====
async def support(update, context):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("🔙 BACK TO MENU", callback_data='back')]]
    text = """📞 SUPPORT CENTER

❓ How to earn credits?
1️⃣ Refer friends → 15 credits each
2️⃣ Every 5 referrals → 25 bonus credits
3️⃣ Redeem promo codes

❓ How to get VPS hosting?
1️⃣ Earn 50+ credits
2️⃣ Buy a plan from PLANS menu
3️⃣ VPS is created automatically!

❓ Need help?
Contact admin: @Free_hostingbyreferbot

Commands:
/start - Main menu
/menu - Show menu
/redeem - Redeem code"""
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ===== ADMIN =====
async def admin_panel(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!", parse_mode='Markdown')
        return
    
    total = get_total()
    keyboard = [
        [InlineKeyboardButton("🔑 GENERATE CODE", callback_data='gen_code')],
        [InlineKeyboardButton("📊 STATISTICS", callback_data='stats')]
    ]
    await update.message.reply_text(
        f"🛠️ ADMIN PANEL\n━━━━━━━━━━━━━━━━━━━━━\n\n👥 Users: `{total}`",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_gen_code(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔑 Generate Code\n\nSend: `/gencode AMOUNT`\nExample: `/gencode 100`",
        parse_mode='Markdown'
    )

async def admin_stats(update, context):
    query = update.callback_query
    await query.answer()
    total = get_total()
    total_bal = float(get_total_balance()) if get_total_balance() else 0
    unused = get_unused_codes()
    await query.edit_message_text(
        f"📊 BOT STATISTICS\n━━━━━━━━━━━━━━━━━━━━━\n\n👥 Users: `{total}`\n💰 Balance: `{total_bal:.2f}`\n🎁 Unused Codes: `{unused}`",
        parse_mode='Markdown'
    )

async def generate_code(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("❌ Usage: `/gencode AMOUNT`\nExample: `/gencode 100`", parse_mode='Markdown')
        return
    amount = float(context.args[0])
    code = gen_code(amount, ADMIN_ID)
    if code:
        await update.message.reply_text(
            f"✅ Code Generated!\n\n🔑 Code: `{code}`\n💰 Amount: `{amount}` Credits\n\nShare with users:\n`/redeem {code}`",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ Error generating code!")

async def gencode_direct(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!", parse_mode='Markdown')
        return
    if not context.args:
        await update.message.reply_text("❌ Usage: `/gencode AMOUNT`\nExample: `/gencode 100`", parse_mode='Markdown')
        return
    try:
        amount = float(context.args[0])
        code = gen_code(amount, ADMIN_ID)
        if code:
            await update.message.reply_text(
                f"✅ Code Generated!\n\n🔑 Code: `{code}`\n💰 Amount: `{amount}` Credits",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Error generating code!")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def stats_direct(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!", parse_mode='Markdown')
        return
    total = get_total()
    total_bal = float(get_total_balance()) if get_total_balance() else 0
    unused = get_unused_codes()
    await update.message.reply_text(
        f"📊 STATISTICS\n━━━━━━━━━━━━━━━━━━━━━\n\n👥 Users: `{total}`\n💰 Balance: `{total_bal:.2f}`\n🎁 Unused Codes: `{unused}`",
        parse_mode='Markdown'
    )

# ===== MAIN =====
def main():
    print("🚀 Starting bot...")
    
    # Check if RAW is installed
    if check_raw_installed():
        print("✅ RAW CLI is installed")
    else:
        print("⚠️ RAW CLI not found. VPS creation will fail.")
        print("   To install: npm install -g rawhq")
        print("   Then run: raw init")
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("redeem", redeem_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("gencode", gencode_direct))
    app.add_handler(CommandHandler("stats", stats_direct))
    
    app.add_handler(CallbackQueryHandler(show_plans, pattern='^plans$'))
    app.add_handler(CallbackQueryHandler(profile, pattern='^profile$'))
    app.add_handler(CallbackQueryHandler(redeem, pattern='^redeem$'))
    app.add_handler(CallbackQueryHandler(referral, pattern='^referral$'))
    app.add_handler(CallbackQueryHandler(leaderboard, pattern='^leaderboard$'))
    app.add_handler(CallbackQueryHandler(support, pattern='^support$'))
    app.add_handler(CallbackQueryHandler(my_hosting, pattern='^my_hosting$'))
    app.add_handler(CallbackQueryHandler(back, pattern='^back$'))
    app.add_handler(CallbackQueryHandler(buy_plan, pattern='^buy_'))
    app.add_handler(CallbackQueryHandler(admin_gen_code, pattern='^gen_code$'))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern='^stats$'))
    
    print("🤖 Bot is running!")
    app.run_polling()

if __name__ == "__main__":
    main()