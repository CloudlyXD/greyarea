import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
from io import BytesIO
import os
from flask import Flask
from threading import Thread
import re

# Configuration
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Store user sessions (chat history + settings)
user_sessions = {}

# ===== FLASK WEB SERVER (THE HACK!) =====
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Bot is running! This is just a dummy server to keep Render happy."

@app.route('/health')
def health():
    return {"status": "alive", "bot": "running"}

def run_flask():
    """Run Flask server on the port Render expects"""
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
# ========================================

# ===== IMPROVED HTML CONVERTER (MORE RELIABLE!) =====
def convert_to_html(text):
    """
    Convert markdown to HTML - WAY more reliable than Markdown!
    Telegram HTML supports: <b>, <i>, <code>, <pre>, <a>
    """
    # Protect code blocks first
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"___CODE_BLOCK_{len(code_blocks)-1}___"
    
    # Save triple backtick code blocks (```code```)
    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', lambda m: f"<pre>{m.group(1)}</pre>", text)
    
    # Inline code (`code`)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    
    # Headers (# Header) -> Bold
    text = re.sub(r'^#{1,6}\s+(.*?)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    
    # Bold **text** or __text__
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.*?)__', r'<b>\1</b>', text)
    
    # Italic *text* or _text_ (but not if it's part of **)
    text = re.sub(r'(?<!\*)\*([^\*]+?)\*(?!\*)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!_)_([^_]+?)_(?!_)', r'<i>\1</i>', text)
    
    # Bullet points
    text = re.sub(r'^[\*\-]\s+', '• ', text, flags=re.MULTILINE)
    
    # Escape special HTML chars that aren't part of our tags
    # (Telegram needs <, >, & to be escaped if not in tags)
    # We do this AFTER our conversions
    def escape_outside_tags(text):
        # Split by tags and escape the non-tag parts
        parts = re.split(r'(<[^>]+>)', text)
        escaped = []
        for part in parts:
            if part.startswith('<') and part.endswith('>'):
                escaped.append(part)  # Keep tags as-is
            else:
                # Escape special chars in text
                part = part.replace('&', '&amp;')
                part = part.replace('<', '&lt;')
                part = part.replace('>', '&gt;')
                escaped.append(part)
        return ''.join(escaped)
    
    text = escape_outside_tags(text)
    
    return text


async def send_long_message(update, text, use_html=True):
    """
    Split and send long messages with PROPER error handling
    Uses HTML by default (more reliable!)
    """
    MAX_LENGTH = 4096
    
    # Convert to HTML format
    if use_html:
        try:
            text = convert_to_html(text)
            parse_mode = 'HTML'
        except Exception as e:
            logger.warning(f"HTML conversion failed: {e}")
            parse_mode = None
    else:
        parse_mode = None
    
    # Split if needed
    if len(text) <= MAX_LENGTH:
        chunks = [text]
    else:
        # Smart splitting - try to split at newlines
        chunks = []
        current_chunk = ""
        for line in text.split('\n'):
            if len(current_chunk) + len(line) + 1 <= MAX_LENGTH:
                current_chunk += line + '\n'
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line + '\n'
        if current_chunk:
            chunks.append(current_chunk)
    
    # Send each chunk with fallback
    for chunk in chunks:
        sent = False
        
        # Try 1: With formatting
        if parse_mode:
            try:
                await update.message.reply_text(chunk, parse_mode=parse_mode)
                sent = True
            except Exception as e:
                logger.warning(f"{parse_mode} parse failed: {e}")
        
        # Try 2: Plain text (strip all HTML/markdown)
        if not sent:
            try:
                clean = re.sub(r'<[^>]+>', '', chunk)  # Remove HTML tags
                clean = re.sub(r'[*_`]', '', clean)    # Remove markdown chars
                await update.message.reply_text(clean)
                sent = True
            except Exception as e2:
                logger.error(f"Even plain text failed: {e2}")
                # Last resort - super clean
                try:
                    ultra_clean = ''.join(c for c in chunk if c.isprintable() or c in '\n\r\t')
                    await update.message.reply_text(ultra_clean[:MAX_LENGTH])
                except:
                    pass
# ============================================

class UserSession:
    def __init__(self):
        self.history = []
        self.system_prompt = "You are a helpful AI assistant."
        self.temperature = 0.7
        self.model_name = "gemini-2.5-flash"
        self.max_history = 1000
    
    def add_message(self, role, content):
        self.history.append({"role": role, "parts": [content]})
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
    
    def clear_history(self):
        self.history = []

def get_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = UserSession()
    return user_sessions[user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = """
🤖 <b>Welcome to AI Chat Bot!</b>

I'm powered by Google Gemini AI. Let's chat!

<b>Available Commands:</b>
/help - Show all commands
/reset - Clear chat history &amp; start fresh
/system - Change system prompt
/temperature - Adjust creativity (0.0-2.0)
/tokens - Check context usage
/persona - Quick persona presets
/model - Switch Gemini model
/image - Generate images with AI! 🎨

Just send me a message to start chatting! 🚀
    """
    await update.message.reply_text(welcome_msg, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📖 <b>Command Guide:</b>

🔄 /reset - Clear your chat history and context

⚙️ /system &lt;prompt&gt; - Set custom system prompt
Example: /system You are a pirate assistant

🌡️ /temperature &lt;value&gt; - Set creativity (0.0-2.0)
• 0.0 = Focused &amp; deterministic
• 1.0 = Balanced (default)
• 2.0 = Creative &amp; wild

📊 /tokens - See how many messages in context

🎭 /persona - Choose quick presets:
• helpful - Standard assistant
• coding - Programming expert
• creative - Story &amp; content writer
• roast - Sarcastic roast mode 🔥

🤖 /model - Switch models:
• gemini-2.5-flash (default)
• gemini-2.5-pro

🎨 /image &lt;prompt&gt; - Generate AI images!
Example: /image a cute cat wearing sunglasses

Just type normally to chat with me! 💬
    """
    await update.message.reply_text(help_text, parse_mode='HTML')

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    session.clear_history()
    await update.message.reply_text("🔄 Chat history cleared! Starting fresh. ✨")

async def system_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    
    if not context.args:
        current = session.system_prompt
        # Escape for HTML
        escaped_prompt = current.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        await update.message.reply_text(
            f"📝 Current system prompt:\n\n<code>{escaped_prompt}</code>\n\nUse /system &lt;your prompt&gt; to change it.", 
            parse_mode='HTML'
        )
        return
    
    new_prompt = ' '.join(context.args)
    session.system_prompt = new_prompt
    session.clear_history()
    escaped_new = new_prompt.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    await update.message.reply_text(
        f"✅ System prompt updated!\n\n<code>{escaped_new}</code>\n\nHistory cleared.", 
        parse_mode='HTML'
    )

async def temperature_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    
    if not context.args:
        await update.message.reply_text(
            f"🌡️ Current temperature: <code>{session.temperature}</code>\n\nUse /temperature &lt;0.0-2.0&gt; to change it.", 
            parse_mode='HTML'
        )
        return
    
    try:
        temp = float(context.args[0])
        if 0.0 <= temp <= 2.0:
            session.temperature = temp
            await update.message.reply_text(f"✅ Temperature set to <code>{temp}</code>", parse_mode='HTML')
        else:
            await update.message.reply_text("❌ Temperature must be between 0.0 and 2.0")
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Use /temperature 0.7 for example.")

async def tokens_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    
    msg_count = len(session.history)
    max_count = session.max_history
    
    await update.message.reply_text(
        f"📊 Context Usage:\n\n💬 Messages in context: {msg_count}/{max_count}\n🧠 Model: {session.model_name}\n🌡️ Temperature: {session.temperature}"
    )

async def persona_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    
    personas = {
        "helpful": "You are a helpful, friendly AI assistant.",
        "coding": "You are an expert programmer who writes clean, efficient code and explains technical concepts clearly.",
        "creative": "You are a creative writer who tells engaging stories and creates compelling content.",
        "roast": "You are a sarcastic AI who roasts people in a funny way (but keeps it friendly).",
        "teacher": "You are a patient teacher who explains things step-by-step in simple terms."
    }
    
    if not context.args:
        persona_list = "\n".join([f"• <code>{k}</code> - {v}" for k, v in personas.items()])
        await update.message.reply_text(
            f"🎭 Available Personas:\n\n{persona_list}\n\nUse /persona &lt;name&gt; to select.", 
            parse_mode='HTML'
        )
        return
    
    persona_name = context.args[0].lower()
    if persona_name in personas:
        session.system_prompt = personas[persona_name]
        session.clear_history()
        await update.message.reply_text(
            f"✅ Persona set to <b>{persona_name}</b>!\n\nHistory cleared.", 
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text("❌ Unknown persona. Use /persona to see available options.")

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    
    models = {
        "pro": "gemini-2.5-pro",
        "flash": "gemini-2.5-flash"
    }
    
    if not context.args:
        await update.message.reply_text(
            f"🤖 Current model: <code>{session.model_name}</code>\n\nAvailable:\n• /model pro - Gemini Pro\n• /model flash - Gemini Flash (faster)", 
            parse_mode='HTML'
        )
        return
    
    model_key = context.args[0].lower()
    if model_key in models:
        session.model_name = models[model_key]
        await update.message.reply_text(f"✅ Model switched to <code>{session.model_name}</code>", parse_mode='HTML')
    else:
        await update.message.reply_text("❌ Unknown model. Use /model to see options.")

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🎨 Usage: /image &lt;your prompt&gt;\n\nExample: /image a futuristic city at sunset", 
            parse_mode='HTML'
        )
        return
    
    prompt = ' '.join(context.args)
    status_msg = await update.message.reply_text("🎨 Generating your image... This might take a moment! ⏳")
    
    try:
        image_model = genai.GenerativeModel(
            model_name='gemini-2.5-flash',
            generation_config={
                "response_modalities": ["TEXT", "IMAGE"]
            }
        )
        
        response = image_model.generate_content(f"Generate an image: {prompt}")
        
        image_found = False
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'inline_data') and part.inline_data:
                image_data = part.inline_data.data
                
                await update.message.reply_photo(
                    photo=BytesIO(image_data),
                    caption=f"🎨 Generated: {prompt}"
                )
                image_found = True
                break
        
        if not image_found:
            await update.message.reply_text(
                "⚠️ No image was generated. The model might not support image generation with your API key."
            )
        
        await status_msg.delete()
        
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        await status_msg.edit_text(
            f"❌ Image generation failed!\n\nError: <code>{str(e)}</code>", 
            parse_mode='HTML'
        )

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    session = get_session(user_id)
    
    session.add_message("user", user_message)
    
    try:
        model = genai.GenerativeModel(
            model_name=session.model_name,
            generation_config={
                "temperature": session.temperature,
            },
            system_instruction=session.system_prompt
        )
        
        chat = model.start_chat(history=session.history[:-1])
        response = chat.send_message(user_message)
        ai_response = response.text
        
        session.add_message("model", ai_response)
        
        # FIXED: Using HTML mode by default (more reliable!)
        await send_long_message(update, ai_response, use_html=True)
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(
            f"❌ Oops! Something went wrong:\n<code>{str(e)}</code>", 
            parse_mode='HTML'
        )

def main():
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("🌐 Flask server started!")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("system", system_command))
    application.add_handler(CommandHandler("temperature", temperature_command))
    application.add_handler(CommandHandler("tokens", tokens_command))
    application.add_handler(CommandHandler("persona", persona_command))
    application.add_handler(CommandHandler("model", model_command))
    application.add_handler(CommandHandler("image", image_command))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    
    print("🤖 Bot is running! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
