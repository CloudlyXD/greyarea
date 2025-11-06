import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
from io import BytesIO
import os
from flask import Flask
from threading import Thread

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

class UserSession:
    def __init__(self):
        self.history = []
        self.system_prompt = "You are a helpful AI assistant."
        self.temperature = 0.7
        self.model_name = "gemini-2.5-flash"
        self.max_history = 1000  # Keep last 20 messages for context
    
    def add_message(self, role, content):
        self.history.append({"role": role, "parts": [content]})
        # Keep only recent messages to avoid token limits
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
    
    def clear_history(self):
        self.history = []

def get_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = UserSession()
    return user_sessions[user_id]

async def send_long_message(update, text):
    """Split and send long messages to handle Telegram's 4096 character limit"""
    MAX_LENGTH = 4096
    
    if len(text) <= MAX_LENGTH:
        await update.message.reply_text(text)
    else:
        # Split into chunks
        for i in range(0, len(text), MAX_LENGTH):
            chunk = text[i:i+MAX_LENGTH]
            await update.message.reply_text(chunk)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = """
🤖 *Welcome to AI Chat Bot!*

I'm powered by Google Gemini AI. Let's chat!

*Available Commands:*
/help - Show all commands
/reset - Clear chat history & start fresh
/system - Change system prompt
/temperature - Adjust creativity (0.0-2.0)
/tokens - Check context usage
/persona - Quick persona presets
/model - Switch Gemini model
/image - Generate images with AI! 🎨

Just send me a message to start chatting! 🚀
    """
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📖 *Command Guide:*

🔄 `/reset` - Clear your chat history and context

⚙️ `/system <prompt>` - Set custom system prompt
Example: `/system You are a pirate assistant`

🌡️ `/temperature <value>` - Set creativity (0.0-2.0)
• 0.0 = Focused & deterministic
• 1.0 = Balanced (default)
• 2.0 = Creative & wild

📊 `/tokens` - See how many messages in context

🎭 `/persona` - Choose quick presets:
• helpful - Standard assistant
• coding - Programming expert
• creative - Story & content writer
• roast - Sarcastic roast mode 🔥

🤖 `/model` - Switch models:
• gemini-2.5-flash (default)
• gemini-2.5-pro

🎨 `/image <prompt>` - Generate AI images!
Example: `/image a cute cat wearing sunglasses`

Just type normally to chat with me! 💬
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

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
        await update.message.reply_text(f"📝 Current system prompt:\n\n`{current}`\n\nUse `/system <your prompt>` to change it.", parse_mode='Markdown')
        return
    
    new_prompt = ' '.join(context.args)
    session.system_prompt = new_prompt
    session.clear_history()  # Clear history when changing system prompt
    await update.message.reply_text(f"✅ System prompt updated!\n\n`{new_prompt}`\n\nHistory cleared.", parse_mode='Markdown')

async def temperature_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    
    if not context.args:
        await update.message.reply_text(f"🌡️ Current temperature: `{session.temperature}`\n\nUse `/temperature <0.0-2.0>` to change it.", parse_mode='Markdown')
        return
    
    try:
        temp = float(context.args[0])
        if 0.0 <= temp <= 2.0:
            session.temperature = temp
            await update.message.reply_text(f"✅ Temperature set to `{temp}`", parse_mode='Markdown')
        else:
            await update.message.reply_text("❌ Temperature must be between 0.0 and 2.0")
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Use `/temperature 0.7` for example.")

async def tokens_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    
    msg_count = len(session.history)
    max_count = session.max_history
    
    await update.message.reply_text(f"📊 Context Usage:\n\n💬 Messages in context: {msg_count}/{max_count}\n🧠 Model: {session.model_name}\n🌡️ Temperature: {session.temperature}")

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
        persona_list = "\n".join([f"• `{k}` - {v}" for k, v in personas.items()])
        await update.message.reply_text(f"🎭 Available Personas:\n\n{persona_list}\n\nUse `/persona <name>` to select.", parse_mode='Markdown')
        return
    
    persona_name = context.args[0].lower()
    if persona_name in personas:
        session.system_prompt = personas[persona_name]
        session.clear_history()
        await update.message.reply_text(f"✅ Persona set to *{persona_name}*!\n\nHistory cleared.", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Unknown persona. Use `/persona` to see available options.")

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    
    models = {
        "pro": "gemini-2.5-pro",
        "flash": "gemini-2.5-flash"
    }
    
    if not context.args:
        await update.message.reply_text(f"🤖 Current model: `{session.model_name}`\n\nAvailable:\n• `/model pro` - Gemini Pro\n• `/model flash` - Gemini 2.5 Flash (faster)", parse_mode='Markdown')
        return
    
    model_key = context.args[0].lower()
    if model_key in models:
        session.model_name = models[model_key]
        await update.message.reply_text(f"✅ Model switched to `{session.model_name}`", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Unknown model. Use `/model` to see options.")

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🎨 Usage: `/image <your prompt>`\n\nExample: `/image a futuristic city at sunset`", parse_mode='Markdown')
        return
    
    prompt = ' '.join(context.args)
    
    # Send "generating" message
    status_msg = await update.message.reply_text("🎨 Generating your image... This might take a moment! ⏳")
    
    try:
        # Try using Gemini 2.0 Flash experimental with image generation
        # This model can generate images inline with responses
        image_model = genai.GenerativeModel(
            model_name='gemini-2.5-flash',
            generation_config={
                "response_modalities": ["TEXT", "IMAGE"]
            }
        )
        
        response = image_model.generate_content(f"Generate an image: {prompt}")
        
        # Extract image from response
        image_found = False
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'inline_data') and part.inline_data:
                # Got an image!
                image_data = part.inline_data.data
                
                # Send the image
                await update.message.reply_photo(
                    photo=BytesIO(image_data),
                    caption=f"🎨 Generated: {prompt}"
                )
                image_found = True
                break
        
        if not image_found:
            await update.message.reply_text("⚠️ No image was generated. The model might not support image generation with your API key. Try upgrading to a paid plan for full image generation access!")
        
        # Delete status message
        await status_msg.delete()
        
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        await status_msg.edit_text(f"❌ Image generation failed!\n\nError: `{str(e)}`\n\n💡 Tip: Image generation might require a paid API key. Check Google AI Studio for details.", parse_mode='Markdown')

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    session = get_session(user_id)
    
    # Add user message to history
    session.add_message("user", user_message)
    
    try:
        # Create model with current settings
        model = genai.GenerativeModel(
            model_name=session.model_name,
            generation_config={
                "temperature": session.temperature,
            },
            system_instruction=session.system_prompt
        )
        
        # Create chat with history
        chat = model.start_chat(history=session.history[:-1])  # Exclude the message we just added
        
        # Get response
        response = chat.send_message(user_message)
        ai_response = response.text
        
        # Add AI response to history
        session.add_message("model", ai_response)
        
        # Send response (handles long messages automatically)
        await send_long_message(update, ai_response)
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Oops! Something went wrong:\n`{str(e)}`", parse_mode='Markdown')

def main():
    # Start Flask server in a separate thread
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("🌐 Flask server started!")
    
    # Create application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("system", system_command))
    application.add_handler(CommandHandler("temperature", temperature_command))
    application.add_handler(CommandHandler("tokens", tokens_command))
    application.add_handler(CommandHandler("persona", persona_command))
    application.add_handler(CommandHandler("model", model_command))
    application.add_handler(CommandHandler("image", image_command))
    
    # Add message handler for chat
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    
    # Start bot
    print("🤖 Bot is running! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
