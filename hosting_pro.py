#!/usr/bin/env python3
# Professional Python Hosting Bot – Admin Only
# Developer: LaMinPaing | Telegram: @OFFICAL_LAMINPAING

import os
import sys
import asyncio
import json
import sqlite3
import shlex
import subprocess
import time
import psutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from contextlib import asynccontextmanager

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)

# ================= CONFIGURATION =================
BOT_TOKEN = "8671958338:AAG9rXGUw8QlAMpYBjMrx815XzPxNo-MSZE"
ADMIN_IDS = [8662212642] 

# Paths
BASE_DIR = Path(__file__).parent.resolve()
UPLOAD_DIR = BASE_DIR / "uploads"
LOGS_DIR = BASE_DIR / "logs"
DB_PATH = BASE_DIR / "data.db"
MAX_FILE_SIZE = 1_000_000                  # 1 MB
MAX_CONCURRENT = 10                         # တစ်ပြိုင်နက် အများဆုံး run ခွင့်

# States for conversation
WAITING_FOR_FILE = 1

# ================= DATABASE SETUP =================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE,
            uploaded_at TEXT,
            last_run TEXT,
            run_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'idle'
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS processes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            pid INTEGER,
            started_at TEXT,
            status TEXT,
            log_file TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS run_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            timestamp TEXT,
            output TEXT,
            error TEXT,
            duration REAL
        )
    ''')
    conn.commit()
    conn.close()

def db_insert_file(filename: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO files (filename, uploaded_at, status) VALUES (?, ?, ?)",
              (filename, datetime.now().isoformat(), 'uploaded'))
    conn.commit()
    conn.close()

def db_update_file_status(filename: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE files SET status = ?, last_run = ? WHERE filename = ?",
              (status, datetime.now().isoformat(), filename))
    conn.commit()
    conn.close()

def db_increment_run_count(filename: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE files SET run_count = run_count + 1 WHERE filename = ?", (filename,))
    conn.commit()
    conn.close()

def db_add_process(filename: str, pid: int, log_file: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO processes (filename, pid, started_at, status, log_file) VALUES (?, ?, ?, ?, ?)",
              (filename, pid, datetime.now().isoformat(), 'running', log_file))
    conn.commit()
    conn.close()

def db_remove_process(filename: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE processes SET status = 'stopped' WHERE filename = ? AND status = 'running'", (filename,))
    conn.commit()
    conn.close()

def db_get_running_processes() -> List[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT filename, pid, started_at FROM processes WHERE status = 'running'")
    rows = c.fetchall()
    conn.close()
    return rows

def db_add_run_log(filename: str, output: str, error: str, duration: float):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO run_logs (filename, timestamp, output, error, duration) VALUES (?, ?, ?, ?, ?)",
              (filename, datetime.now().isoformat(), output[:5000], error[:5000], duration))
    conn.commit()
    conn.close()

def db_get_file_list() -> List[str]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT filename FROM files ORDER BY uploaded_at DESC")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

# ================= HELPER FUNCTIONS =================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def sanitize_filename(filename: str) -> str:
    return Path(filename).name

def get_log_file_path(filename: str) -> Path:
    LOGS_DIR.mkdir(exist_ok=True)
    return LOGS_DIR / f"{filename}_{int(time.time())}.log"

async def run_python_file(file_path: Path, log_path: Path) -> Tuple[int, str, str, float]:
    """Run Python file and capture output, error, duration."""
    start_time = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(file_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=file_path.parent
        )
        stdout, stderr = await proc.communicate()
        duration = time.time() - start_time
        output = stdout.decode('utf-8', errors='replace')
        error = stderr.decode('utf-8', errors='replace')
        # Save to log file
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"=== STDOUT ===\n{output}\n\n=== STDERR ===\n{error}\n")
        return proc.returncode, output, error, duration
    except Exception as e:
        duration = time.time() - start_time
        error = str(e)
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"EXCEPTION: {error}\n")
        return -1, "", error, duration

# ================= COMMAND HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ ခင်ဗျားကို ခွင့်မပြုပါ။")
        return

    text = """
*🤖 Professional Python Hosting Bot*

ဒီ bot က Python ဖိုင်တွေကို အလုံခြုံဆုံး run ပေးပြီး log များ၊ statistics များကို ခြေရာခံနိုင်ပါတယ်။

*Commands:*
• `/upload` – Python ဖိုင် (.py) တင်ရန်
• `/run <filename>` – ဖိုင်ကို run ရန်
• `/stop <filename>` – run နေတဲ့ဖိုင်ကို ရပ်ရန်
• `/stopall` – အားလုံးရပ်ရန်
• `/list` – ရှိသမျှဖိုင်များစာရင်း
• `/status` – run နေတဲ့ process များ
• `/logs <filename>` – နောက်ဆုံး run log ကိုကြည့်ရန်
• `/stats` – စုစုပေါင်း run အရေအတွက်၊ success/fail
• `/delete <filename>` – ဖိုင်ဖျက်ရန်
• `/help` – ဒီ message ကိုပြရန်

_Admin Only_
    """
    await update.message.reply_text(text, parse_mode='Markdown')

async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ ခွင့်မပြုပါ။")
        return
    await update.message.reply_text("📤 Python (.py) ဖိုင်ကို တင်ပေးပါ။ (Max 1MB)")
    return WAITING_FOR_FILE

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ ခွင့်မပြုပါ။")
        return ConversationHandler.END

    doc = update.message.document
    if not doc or not doc.file_name.endswith('.py'):
        await update.message.reply_text("❌ Python (.py) ဖိုင်သာ တင်လို့ရပါတယ်။")
        return ConversationHandler.END

    if doc.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"❌ ဖိုင်အရွယ်အစား {MAX_FILE_SIZE//1000} KB ထက်မကြီးရပါ။")
        return ConversationHandler.END

    safe_name = sanitize_filename(doc.file_name)
    file_path = UPLOAD_DIR / safe_name
    UPLOAD_DIR.mkdir(exist_ok=True)

    file = await context.bot.get_file(doc.file_id)
    await file.download_to_drive(file_path)

    db_insert_file(safe_name)
    await update.message.reply_text(f"✅ `{safe_name}` တင်ပြီးပါပြီ။\n/run {safe_name} နဲ့ run လို့ရပါတယ်။", parse_mode='Markdown')
    return ConversationHandler.END

async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ ခွင့်မပြုပါ။")
        return

    if not context.args:
        await update.message.reply_text("❌ ဖိုင်နာမည်ထည့်ပါ။ /run <filename>")
        return

    filename = context.args[0]
    safe_name = sanitize_filename(filename)
    file_path = UPLOAD_DIR / safe_name

    if not file_path.exists():
        await update.message.reply_text(f"❌ `{safe_name}` ဖိုင်မတွေ့ပါ။", parse_mode='Markdown')
        return

    # Check running processes limit
    running = db_get_running_processes()
    if len(running) >= MAX_CONCURRENT:
        await update.message.reply_text(f"⏳ တစ်ချိန် {MAX_CONCURRENT} ခုသာ run လို့ရပါတယ်။ နောက်မှထပ်စမ်းပါ။")
        return

    status_msg = await update.message.reply_text(f"🚀 `{safe_name}` ကို run နေပါသည်...", parse_mode='Markdown')
    log_path = get_log_file_path(safe_name)
    db_increment_run_count(safe_name)
    db_update_file_status(safe_name, 'running')

    # Run the file
    returncode, stdout, stderr, duration = await run_python_file(file_path, log_path)

    db_update_file_status(safe_name, 'completed' if returncode == 0 else 'failed')
    db_add_run_log(safe_name, stdout, stderr, duration)

    # Prepare result message
    result_text = f"*📝 Result for `{safe_name}`*\n"
    result_text += f"• Exit code: `{returncode}`\n"
    result_text += f"• Duration: `{duration:.2f}` seconds\n"
    if stdout:
        result_text += f"\n*Output:*\n```\n{stdout[:1500]}\n```\n"
    if stderr:
        result_text += f"\n*Error:*\n```\n{stderr[:1500]}\n```\n"

    # Send result (split if too long)
    if len(result_text) > 4000:
        with open(log_path, 'r') as f:
            await update.message.reply_document(document=f, filename=f"{safe_name}_log.txt", caption=f"Log for {safe_name}")
        await status_msg.edit_text(f"✅ `{safe_name}` run ပြီးပါပြီ။ Log ကို file အနေနဲ့ ပို့ပေးလိုက်ပါတယ်။", parse_mode='Markdown')
    else:
        await status_msg.edit_text(result_text, parse_mode='Markdown')

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ ခွင့်မပြုပါ။")
        return

    if not context.args:
        await update.message.reply_text("❌ /stop <filename>")
        return

    filename = context.args[0]
    safe_name = sanitize_filename(filename)

    running = db_get_running_processes()
    for fn, pid, started in running:
        if fn == safe_name:
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                proc.wait(timeout=5)
            except:
                pass
            db_remove_process(safe_name)
            await update.message.reply_text(f"⏹️ `{safe_name}` ရပ်လိုက်ပါပြီ။", parse_mode='Markdown')
            return

    await update.message.reply_text(f"❌ `{safe_name}` က run နေတာမတွေ့ပါ။", parse_mode='Markdown')

async def stopall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ ခွင့်မပြုပါ။")
        return

    running = db_get_running_processes()
    if not running:
        await update.message.reply_text("⏸️ run နေတဲ့ process မရှိပါ။")
        return

    for fn, pid, started in running:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
        except:
            pass
        db_remove_process(fn)

    await update.message.reply_text(f"⏹️ run နေတဲ့ process {len(running)} ခုလုံးရပ်လိုက်ပါပြီ။")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ ခွင့်မပြုပါ။")
        return

    files = db_get_file_list()
    if not files:
        await update.message.reply_text("📂 ဖိုင်မရှိသေးပါ။ /upload နဲ့ တင်ပါ။")
        return

    running_filenames = [fn for fn, _, _ in db_get_running_processes()]
    text = "*📁 Python Files:*\n\n"
    for f in files:
        status = "🟢 running" if f in running_filenames else "⚪ idle"
        text += f"• `{f}` – {status}\n"

    await update.message.reply_text(text, parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ ခွင့်မပြုပါ။")
        return

    running = db_get_running_processes()
    if not running:
        await update.message.reply_text("⏸️ run နေတဲ့ process မရှိပါ။")
        return

    text = "*🟢 Running Processes:*\n\n"
    for fn, pid, started in running:
        text += f"• `{fn}` (PID: {pid})\n  Started: {started}\n"

    await update.message.reply_text(text, parse_mode='Markdown')

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ ခွင့်မပြုပါ။")
        return

    if not context.args:
        await update.message.reply_text("❌ /logs <filename>")
        return

    filename = context.args[0]
    safe_name = sanitize_filename(filename)

    # Find latest log file
    log_files = sorted(LOGS_DIR.glob(f"{safe_name}_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not log_files:
        await update.message.reply_text(f"❌ `{safe_name}` အတွက် log မရှိပါ။", parse_mode='Markdown')
        return

    latest = log_files[0]
    with open(latest, 'r') as f:
        content = f.read()

    if len(content) > 4000:
        await update.message.reply_document(document=latest, caption=f"Log for {safe_name}")
    else:
        await update.message.reply_text(f"*Log for `{safe_name}`:*\n```\n{content}\n```", parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ ခွင့်မပြုပါ။")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM files")
    total_files = c.fetchone()[0]
    c.execute("SELECT SUM(run_count) FROM files")
    total_runs = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM run_logs WHERE error != '' AND error IS NOT NULL")
    failed_runs = c.fetchone()[0]
    success_runs = total_runs - failed_runs
    conn.close()

    text = f"*📊 Statistics*\n\n"
    text += f"• Total files: `{total_files}`\n"
    text += f"• Total runs: `{total_runs}`\n"
    text += f"• ✅ Success: `{success_runs}`\n"
    text += f"• ❌ Failed: `{failed_runs}`\n"
    await update.message.reply_text(text, parse_mode='Markdown')

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ ခွင့်မပြုပါ။")
        return

    if not context.args:
        await update.message.reply_text("❌ /delete <filename>")
        return

    filename = context.args[0]
    safe_name = sanitize_filename(filename)
    file_path = UPLOAD_DIR / safe_name

    if not file_path.exists():
        await update.message.reply_text(f"❌ `{safe_name}` ဖိုင်မတွေ့ပါ။", parse_mode='Markdown')
        return

    # Check if running
    running = db_get_running_processes()
    if any(fn == safe_name for fn, _, _ in running):
        await update.message.reply_text(f"⚠️ `{safe_name}` က run နေတုန်းပါ။ အရင်ရပ်ပါ။", parse_mode='Markdown')
        return

    file_path.unlink()
    # Remove from DB (optional)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM files WHERE filename = ?", (safe_name,))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🗑️ `{safe_name}` ဖျက်လိုက်ပါပြီ။", parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Upload cancelled.")
    return ConversationHandler.END

# ================= MAIN =================
def main():
    # Create directories
    UPLOAD_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    init_db()

    # Install psutil if not present
    try:
        import psutil
    except ImportError:
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'psutil'], check=True)

    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation for upload
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('upload', upload_command)],
        states={WAITING_FOR_FILE: [MessageHandler(filters.Document.ALL, handle_file_upload)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('run', run_command))
    app.add_handler(CommandHandler('stop', stop_command))
    app.add_handler(CommandHandler('stopall', stopall_command))
    app.add_handler(CommandHandler('list', list_command))
    app.add_handler(CommandHandler('status', status_command))
    app.add_handler(CommandHandler('logs', logs_command))
    app.add_handler(CommandHandler('stats', stats_command))
    app.add_handler(CommandHandler('delete', delete_command))
    app.add_handler(CommandHandler('help', help_command))

    print("🤖 Professional Hosting Bot started...")
    print(f"📁 Upload dir: {UPLOAD_DIR.absolute()}")
    print(f"📄 Logs dir: {LOGS_DIR.absolute()}")
    print(f"🗄️ DB: {DB_PATH.absolute()}")
    app.run_polling()

if __name__ == '__main__':
    main()