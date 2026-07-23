"""
Variable replacement system for Discord bot messages.
Supports dynamic variables like {username}, {ticketnumber}, {category}, etc.
"""

import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import discord


# Available variables documentation
AVAILABLE_VARIABLES = {
    # User variables
    "username": "User's display name",
    "usermention": "User mention (@user)",
    "userid": "User's ID",
    # Ticket variables
    "ticketnumber": "Ticket number (formatted as 0001, 0002, etc.)",
    "category": "Ticket category name",
    "subject": "Ticket subject",
    "description": "Ticket description",
    # Server variables
    "servername": "Server/guild name",
    "serverid": "Server/guild ID",
    # Channel variables
    "channelname": "Channel name",
    "channelmention": "Channel mention (#channel)",
    "channelid": "Channel ID",
    # Time variables
    "timestamp": "Current timestamp (Discord format)",
    "date": "Current date",
    "time": "Current time",
    # Staff variables
    "claimedby": "Staff member who claimed the ticket",
    "closedby": "Staff member who closed the ticket",
}


def replace_variables(text: str, context: Optional[Dict[str, Any]] = None) -> str:
    """
    Replace variables in text with actual values from context.

    Args:
        text: The text containing variables in {variable} format
        context: Dictionary containing variable values

    Returns:
        Text with variables replaced

    Example:
        >>> context = {'username': 'John', 'ticketnumber': 42}
        >>> replace_variables('Hello {username}, ticket #{ticketnumber}', context)
        'Hello John, ticket #0042'
    """
    if not text or not isinstance(text, str):
        return text

    if context is None:
        context = {}

    # Find all variables in the text
    pattern = r"\{([a-zA-Z0-9_]+)\}"

    def replacer(match):
        var_name = match.group(1).lower()

        # User variables
        if var_name == "username":
            user = context.get("user")
            if user:
                return getattr(user, "display_name", getattr(user, "name", "Unknown User"))
            return context.get("username", "Unknown User")

        elif var_name == "usermention":
            user = context.get("user")
            if user:
                return user.mention
            user_id = context.get("user_id") or context.get("userid")
            if user_id:
                return f"<@{user_id}>"
            return "@Unknown User"

        elif var_name == "userid":
            user = context.get("user")
            if user:
                return str(user.id)
            return str(context.get("user_id", context.get("userid", "0")))

        # Ticket variables
        elif var_name == "ticketnumber":
            ticket_num = context.get("ticket_number", context.get("ticketnumber"))
            if ticket_num is not None:
                return f"{int(ticket_num):04d}"
            return "0000"

        elif var_name == "category":
            return str(context.get("category", "N/A"))

        elif var_name == "subject":
            return str(context.get("subject", "N/A"))

        elif var_name == "description":
            return str(context.get("description", "N/A"))

        # Server variables
        elif var_name == "servername":
            guild = context.get("guild")
            if guild:
                return guild.name
            return str(context.get("server_name", context.get("servername", "Unknown Server")))

        elif var_name == "serverid":
            guild = context.get("guild")
            if guild:
                return str(guild.id)
            return str(context.get("server_id", context.get("serverid", "0")))

        # Channel variables
        elif var_name == "channelname":
            channel = context.get("channel")
            if channel:
                return channel.name
            return str(context.get("channel_name", context.get("channelname", "unknown-channel")))

        elif var_name == "channelmention":
            channel = context.get("channel")
            if channel:
                return channel.mention
            channel_id = context.get("channel_id") or context.get("channelid")
            if channel_id:
                return f"<#{channel_id}>"
            return "#unknown-channel"

        elif var_name == "channelid":
            channel = context.get("channel")
            if channel:
                return str(channel.id)
            return str(context.get("channel_id", context.get("channelid", "0")))

        # Time variables
        elif var_name == "timestamp":
            dt = context.get("timestamp", datetime.now(timezone.utc))
            if isinstance(dt, datetime):
                return f"<t:{int(dt.timestamp())}:F>"
            return f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>"

        elif var_name == "date":
            dt = context.get("timestamp", datetime.now(timezone.utc))
            if isinstance(dt, datetime):
                return dt.strftime("%Y-%m-%d")
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")

        elif var_name == "time":
            dt = context.get("timestamp", datetime.now(timezone.utc))
            if isinstance(dt, datetime):
                return dt.strftime("%H:%M:%S UTC")
            return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

        # Staff variables
        elif var_name == "claimedby":
            claimed_by = context.get("claimed_by")
            if claimed_by:
                if isinstance(claimed_by, (discord.Member, discord.User)):
                    return claimed_by.mention
                return str(claimed_by)
            return "N/A"

        elif var_name == "closedby":
            closed_by = context.get("closed_by")
            if closed_by:
                if isinstance(closed_by, (discord.Member, discord.User)):
                    return closed_by.mention
                return str(closed_by)
            return "N/A"

        # If variable not found, return original
        return match.group(0)

    return re.sub(pattern, replacer, text)


def get_available_variables() -> Dict[str, str]:
    """
    Get a dictionary of all available variables and their descriptions.

    Returns:
        Dictionary mapping variable names to descriptions
    """
    return AVAILABLE_VARIABLES.copy()


def format_variables_list() -> str:
    """
    Format the available variables as a readable string.

    Returns:
        Formatted string listing all available variables
    """
    lines = ["**Available Variables:**\n"]

    categories = {
        "User Variables": ["username", "usermention", "userid"],
        "Ticket Variables": ["ticketnumber", "category", "subject", "description"],
        "Server Variables": ["servername", "serverid"],
        "Channel Variables": ["channelname", "channelmention", "channelid"],
        "Time Variables": ["timestamp", "date", "time"],
        "Staff Variables": ["claimedby", "closedby"],
    }

    for category, vars_list in categories.items():
        lines.append(f"\n**{category}:**")
        for var in vars_list:
            if var in AVAILABLE_VARIABLES:
                lines.append(f"- `{{{var}}}` - {AVAILABLE_VARIABLES[var]}")

    return "\n".join(lines)


def build_ticket_context(
    user: Optional[discord.Member] = None,
    ticket_number: Optional[int] = None,
    category: Optional[str] = None,
    subject: Optional[str] = None,
    description: Optional[str] = None,
    guild: Optional[discord.Guild] = None,
    channel: Optional[discord.TextChannel] = None,
    claimed_by: Optional[discord.Member] = None,
    closed_by: Optional[discord.Member] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Build a context dictionary for ticket-related variable replacement.

    Args:
        user: The ticket creator
        ticket_number: Ticket number
        category: Ticket category
        subject: Ticket subject
        description: Ticket description
        guild: Discord guild/server
        channel: Discord channel
        claimed_by: Staff member who claimed the ticket
        closed_by: Staff member who closed the ticket
        **kwargs: Additional context values

    Returns:
        Context dictionary for variable replacement
    """
    context = {
        "user": user,
        "ticket_number": ticket_number,
        "category": category,
        "subject": subject,
        "description": description,
        "guild": guild,
        "channel": channel,
        "claimed_by": claimed_by,
        "closed_by": closed_by,
        "timestamp": datetime.now(timezone.utc),
    }

    # Add any additional context
    context.update(kwargs)

    return context
