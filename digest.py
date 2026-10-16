from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

def print_terminal_digest(emails: list):
    if not emails:
        console.print(Panel("No new emails to process!", title="📬 MailPilot Digest", style="bold green"))
        return

    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("ID", style="dim", width=4)
    table.add_column("Sender & Subject", width=30)
    table.add_column("AI Summary")
    table.add_column("Details", justify="right", width=16)

    for e in emails:
        urgency = e.get("urgency", "NORMAL")
        urgency_color = "red" if urgency == "URGENT" else "yellow" if urgency == "NORMAL" else "white"
        
        cat = e.get("category", "Unknown")
        cat_color = "cyan"
        if cat == "Spam-risk": 
            cat_color = "bold red"
        elif cat == "Finance":
            cat_color = "green"
        
        draft_status = "✍️ Draft saved" if e.get("draft_reply") else "🚫 No draft"

        sender_subj = f"[bold]{e['sender']}[/bold]\n{e['subject']}"
        details = f"[{urgency_color}]{urgency}[/]\n[{cat_color}]{cat}[/]\n[dim]{draft_status}[/dim]"

        table.add_row(
            str(e["id"]),
            sender_subj,
            e.get("summary", ""),
            details
        )

    console.print(Panel(table, title="📬 MailPilot Daily Digest", border_style="blue"))
