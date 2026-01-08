import logging
import re
from datetime import datetime
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from facto.core.enums import ChatMode
from facto.core.prompts import get_system_prompt
from facto.services.ai_service import AIService
from facto.services.memory import MemoryManager

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096


def _split_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split a message into chunks that fit within Telegram's limit."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        split_at = max_length
        newline_pos = text.rfind('\n', 0, max_length)
        if newline_pos > max_length // 2:
            split_at = newline_pos + 1
        else:
            space_pos = text.rfind(' ', 0, max_length)
            if space_pos > max_length // 2:
                split_at = space_pos + 1

        chunks.append(text[:split_at])
        text = text[split_at:]

    return chunks


def _get_date_suffix(day: int) -> str:
    if 11 <= day <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


class TelegramBotHandlers:
    def __init__(self, ai_service: AIService, memory_manager: MemoryManager):
        self.ai_service = ai_service
        self.memory = memory_manager

    async def journal_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process diary entry with journal formatting in a conversational topic flow."""
        if update.message is None:
            return

        logger.info(f"Journal command triggered by user {update.effective_user.id}")

        full_text = update.message.text or ""
        match = re.match(r'^/journal(?:@\w+)?[ \t]*\n*', full_text)
        if match:
            diary_text = full_text[match.end():]
        else:
            diary_text = ""

        diary_text = diary_text.strip()

        if not diary_text:
            await update.message.reply_text(
                "Usage: `/journal <your diary entry>`\n\n"
                "Supports multi-line entries!\n\n"
                "Example:\n"
                "`/journal Today I struggled with time management. "
                "I realize I need better planning. "
                "I will start using a daily planner.`",
                parse_mode="Markdown"
            )
            return

        now = datetime.now()
        day_suffix = _get_date_suffix(now.day)
        current_date = now.strftime(f"%d{day_suffix} %B, %A").lstrip("0").replace(f"0{day_suffix}", day_suffix)

        chat_id = update.effective_chat.id
        self.memory.set_chat_mode(chat_id, ChatMode.JOURNAL)

        diary_with_date = f"Today's date is: {current_date}\n\nMy diary entry:\n{diary_text}"
        await self._create_topic_flow(update, context, diary_with_date)

    async def done_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Finalize the current conversation and close the topic."""
        if update.message is None:
            return

        thread_id = update.message.message_thread_id
        chat_id = update.effective_chat.id

        if thread_id is None:
            await update.message.reply_text("This command only works inside a topic.")
            return

        if not self.memory.is_conversation_active(thread_id):
            await update.message.reply_text("No active conversation in this topic.")
            return

        self.memory.mark_message_for_deletion(thread_id, update.message.message_id)
        self.memory.add_message(thread_id, "user", "I'm satisfied with the current version. Please finalize it now.")
        await self._process_ai_response(chat_id, thread_id, context)

    async def delete_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Delete the current topic."""
        if update.message is None:
            return

        chat_id = update.effective_chat.id
        thread_id = update.message.message_thread_id

        if thread_id is None:
            await update.message.reply_text("This command only works inside a topic.")
            return

        msg_ids = self.memory.get_messages_to_delete(thread_id)
        if msg_ids:
            context.application.create_task(self._delete_history(context.bot, chat_id, msg_ids))

        self.memory.end_conversation(thread_id)

        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
            await context.bot.delete_forum_topic(chat_id=chat_id, message_thread_id=thread_id)
        except Exception as e:
            logger.error(f"Error deleting topic: {e}")
            await update.message.reply_text("Error: Could not delete topic. Check permissions.")

    async def handle_conversation_flow(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle replies inside an active topic."""
        if update.message is None:
            return

        thread_id = update.message.message_thread_id

        if thread_id is None or not self.memory.is_conversation_active(thread_id):
            return

        chat_id = update.effective_chat.id
        user_input = update.message.text

        self.memory.mark_message_for_deletion(thread_id, update.message.message_id)
        self.memory.add_message(thread_id, "user", user_input)
        await self._process_ai_response(chat_id, thread_id, context)

    async def _create_topic_flow(self, update: Update, context: ContextTypes.DEFAULT_TYPE, question: str):
        chat_id = update.effective_chat.id
        user = update.effective_user

        topic_name = (question[:60] + '..') if len(question) > 60 else question

        try:
            topic = await context.bot.create_forum_topic(chat_id=chat_id, name=topic_name)
            thread_id = topic.message_thread_id

            current_mode = self.memory.get_chat_mode(chat_id)
            initial_history = [
                {"role": "system", "content": get_system_prompt(current_mode)},
                {"role": "user", "content": question}
            ]
            self.memory.start_conversation(thread_id, initial_history)

            welcome_msg = await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=f"Hi {user.mention_html()}! Processing your journal entry...",
                parse_mode="HTML"
            )
            self.memory.mark_message_for_deletion(thread_id, welcome_msg.message_id)

            await self._process_ai_response(chat_id, thread_id, context)

        except Exception as e:
            logger.error(f"Error creating topic: {e}")
            await update.message.reply_text("Error: I need 'Manage Topics' admin rights.")

    async def _process_ai_response(self, chat_id, thread_id, context):
        await context.bot.send_chat_action(chat_id=chat_id, message_thread_id=thread_id, action=ChatAction.TYPING)

        try:
            conversation = self.memory.get_conversation(thread_id)
            if not conversation:
                return

            messages = conversation.history.copy()
            ai_reply = self.ai_service.get_response(messages)

            self.memory.add_message(thread_id, "assistant", ai_reply)

            chunks = _split_message(ai_reply)
            for chunk in chunks:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        message_thread_id=thread_id,
                        text=chunk,
                        parse_mode="Markdown"
                    )
                except Exception:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        message_thread_id=thread_id,
                        text=chunk
                    )

        except Exception as e:
            logger.error(f"AI Error: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text="An error occurred while communicating with the AI."
            )

    async def _delete_history(self, bot, chat_id, msg_ids):
        for mid in msg_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=mid)
            except Exception:
                pass
