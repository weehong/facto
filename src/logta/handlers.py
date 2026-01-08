"""Telegram message handlers for the logger bot."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from logta.services.mongodb_service import MongoDBService

if TYPE_CHECKING:
    from logta.services.ai_service import AIService

logger = logging.getLogger(__name__)


class MessageLoggerHandlers:
    """Handlers for logging Telegram messages."""

    def __init__(
        self,
        mongodb_service: MongoDBService,
        owner_id: int,
        ai_service: AIService | None = None,
    ):
        """Initialize handlers with MongoDB service.

        Args:
            mongodb_service: The MongoDB service for storing messages
            owner_id: Telegram user ID of the bot owner
            ai_service: Optional AI service for title generation
        """
        self.db = mongodb_service
        self.owner_id = owner_id
        self.ai_service = ai_service

    async def log_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Log any incoming message to MongoDB.

        This handler captures all message types: text, photos, videos,
        documents, stickers, etc. Automatically activates chats.
        """
        if not update.message:
            return

        # Automatically activate chat if not already activated
        chat_title = update.message.chat.title or "Private Chat"
        self.db.activate_chat(update.message.chat_id, chat_title)

        try:
            # Convert the Telegram message object to a dictionary
            message_data = update.message.to_dict()

            # Add chat_id at root level for easier indexing
            message_data["chat_id"] = update.message.chat_id

            # Add user info at root level for easier querying
            if update.message.from_user:
                message_data["from_user"] = update.message.from_user.to_dict()

            # Save to MongoDB (run in thread to avoid blocking event loop)
            success = await asyncio.to_thread(self.db.save_message, message_data)

            if success:
                user_name = (
                    update.message.from_user.first_name
                    if update.message.from_user
                    else "Unknown"
                )
                chat_title = update.message.chat.title or "Private Chat"
                logger.info(
                    f"Logged message from {user_name} in '{chat_title}' "
                    f"(msg_id: {update.message.message_id})"
                )

        except Exception as e:
            logger.error(f"Error logging message: {e}")

    async def log_edited_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Log edited messages, preserving the edit history."""
        if not update.edited_message:
            return

        # Automatically activate chat if not already activated
        chat_title = update.edited_message.chat.title or "Private Chat"
        self.db.activate_chat(update.edited_message.chat_id, chat_title)

        try:
            message_data = update.edited_message.to_dict()
            message_data["chat_id"] = update.edited_message.chat_id

            if update.edited_message.from_user:
                message_data["from_user"] = update.edited_message.from_user.to_dict()

            success = await asyncio.to_thread(self.db.save_edited_message, message_data)

            if success:
                user_name = (
                    update.edited_message.from_user.first_name
                    if update.edited_message.from_user
                    else "Unknown"
                )
                logger.info(
                    f"Logged edited message from {user_name} "
                    f"(msg_id: {update.edited_message.message_id})"
                )

        except Exception as e:
            logger.error(f"Error logging edited message: {e}")

    async def log_channel_post(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Log channel posts."""
        if not update.channel_post:
            return

        # Automatically activate chat if not already activated
        chat_title = update.channel_post.chat.title or "Unknown Channel"
        self.db.activate_chat(update.channel_post.chat_id, chat_title)

        try:
            message_data = update.channel_post.to_dict()
            message_data["chat_id"] = update.channel_post.chat_id
            message_data["is_channel_post"] = True

            success = await asyncio.to_thread(self.db.save_message, message_data)

            if success:
                channel_title = update.channel_post.chat.title or "Unknown Channel"
                logger.info(
                    f"Logged channel post in '{channel_title}' "
                    f"(msg_id: {update.channel_post.message_id})"
                )

        except Exception as e:
            logger.error(f"Error logging channel post: {e}")

    async def stats_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show logging statistics. Only works for bot owner."""
        if not update.message or not update.message.from_user:
            return

        # Only respond to owner
        if update.message.from_user.id != self.owner_id:
            return

        try:
            chat_id = update.message.chat_id
            total_messages = await asyncio.to_thread(self.db.get_message_count)
            chat_messages = await asyncio.to_thread(self.db.get_message_count, chat_id)
            total_events = await asyncio.to_thread(self.db.get_event_count)
            chat_events = await asyncio.to_thread(self.db.get_event_count, chat_id)

            await update.message.reply_text(
                f"Message Logger Stats:\n"
                f"This chat:\n"
                f"  - Messages: {chat_messages:,}\n"
                f"  - Events: {chat_events:,}\n"
                f"Total:\n"
                f"  - Messages: {total_messages:,}\n"
                f"  - Events: {total_events:,}"
            )

        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            await update.message.reply_text("Failed to retrieve statistics.")

    async def topic_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Create a new forum topic with AI-generated title from the message."""
        if not update.message or not update.message.from_user:
            return

        # Check if message content is provided
        if not context.args:
            await update.message.reply_text(
                "Usage: `/topic <your message>`\n\n"
                "Example: `/topic How do I fix the database connection issue?`",
                parse_mode="Markdown",
            )
            return

        user_message = " ".join(context.args)
        chat_id = update.message.chat_id
        user = update.message.from_user

        # Show typing indicator
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        try:
            # Generate topic title
            if self.ai_service:
                topic_title = await asyncio.to_thread(
                    self.ai_service.generate_topic_title, user_message
                )
            else:
                # Fallback to truncation if no AI service
                topic_title = (
                    (user_message[:57] + "...") if len(user_message) > 60 else user_message
                )

            # Create the forum topic
            topic = await context.bot.create_forum_topic(
                chat_id=chat_id, name=topic_title
            )
            thread_id = topic.message_thread_id

            # Send the original message in the new topic
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=f"Topic created by {user.mention_html()}\n\n{user_message}",
                parse_mode="HTML",
            )

            logger.info(
                f"Created topic '{topic_title}' by {user.first_name} in chat {chat_id}"
            )

        except Exception as e:
            logger.error(f"Error creating topic: {e}")
            await update.message.reply_text(
                "Failed to create topic. Make sure the bot has 'Manage Topics' permission."
            )

    async def history_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Retrieve conversation history for the current topic. Only works for bot owner."""
        if not update.message or not update.message.from_user:
            return

        # Only respond to owner
        if update.message.from_user.id != self.owner_id:
            return

        thread_id = update.message.message_thread_id
        if thread_id is None:
            await update.message.reply_text("This command only works inside a forum topic.")
            return

        try:
            chat_id = update.message.chat_id
            messages = await asyncio.to_thread(
                self.db.get_messages_by_topic, chat_id, thread_id
            )

            if not messages:
                await update.message.reply_text("No messages found for this topic.")
                return

            # Format conversation
            lines = [f"Topic Conversation ({len(messages)} messages):\n"]
            for msg in messages:
                user = msg.get("from_user", {})
                name = user.get("first_name", "Unknown")
                text = msg.get("text") or msg.get("caption") or "[media]"
                # Truncate long messages
                if len(text) > 100:
                    text = text[:100] + "..."
                lines.append(f"• {name}: {text}")

            response = "\n".join(lines)

            # Telegram message limit
            if len(response) > 4096:
                response = response[:4093] + "..."

            await update.message.reply_text(response)

        except Exception as e:
            logger.error(f"Error retrieving topic conversation: {e}")
            await update.message.reply_text("Failed to retrieve topic conversation.")

