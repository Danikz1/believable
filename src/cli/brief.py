"""CLI commands for brief generation and delivery."""

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

brief_app = typer.Typer(help="Brief generation and delivery")
console = Console()


@brief_app.command("generate")
def brief_generate(
    days_back: int = typer.Option(7, help="Days to look back for claims"),
    publish: bool = typer.Option(False, help="Auto-publish the brief"),
):
    """Generate an intelligence brief from approved claims."""
    from src.db.session import get_session
    from src.pipeline.briefs import generate_brief, publish_brief

    session = get_session()
    try:
        console.print("\n[bold cyan]Generating intelligence brief...[/bold cyan]\n")

        brief = generate_brief(session, days_back=days_back)

        if publish:
            publish_brief(session, brief.id)
            console.print(f"[green]✓ Published[/green]")

        # Display
        console.print(Panel(
            Markdown(brief.content_markdown),
            title=f"[bold]{brief.title}[/bold]",
            border_style="cyan",
        ))

        sections = brief.sections or {}
        console.print(f"\n[dim]Headlines: {len(sections.get('headlines', []))} | "
                      f"Shifts: {len(sections.get('shifts', []))} | "
                      f"Topics: {len(sections.get('topic_pulse', []))} | "
                      f"Discoveries: {len(sections.get('discoveries', []))}[/dim]")
        console.print(f"[dim]Claims referenced: {len(brief.claim_ids or [])} | "
                      f"Status: {brief.status}[/dim]\n")

    finally:
        session.close()


@brief_app.command("send")
def brief_send(
    telegram: bool = typer.Option(False, help="Send via Telegram"),
    email: bool = typer.Option(False, help="Send via email"),
    brief_id: str = typer.Option(None, help="Specific brief ID (default: latest)"),
):
    """Send the latest (or specified) brief via Telegram and/or email."""
    from src.db.session import get_session
    from src.db.models import Briefs
    from src.config import settings

    session = get_session()
    try:
        if brief_id:
            brief = session.query(Briefs).filter(Briefs.id == brief_id).first()
        else:
            brief = session.query(Briefs).order_by(Briefs.created_at.desc()).first()

        if not brief:
            console.print("[red]No brief found[/red]")
            raise typer.Exit(1)

        console.print(f"[cyan]Sending: {brief.title}[/cyan]\n")

        if telegram:
            from src.pipeline.delivery import deliver_telegram
            token = getattr(settings, 'telegram_bot_token', None)
            chat = getattr(settings, 'telegram_chat_id', None)
            if token and chat:
                if deliver_telegram(session, brief, token, chat):
                    console.print("[green]✓ Telegram delivered[/green]")
                else:
                    console.print("[red]✗ Telegram failed[/red]")
            else:
                console.print("[yellow]⚠ Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)[/yellow]")

        if email:
            from src.pipeline.delivery import deliver_email
            smtp_host = getattr(settings, 'smtp_host', None)
            if smtp_host:
                from_addr = getattr(settings, 'email_from', '') or getattr(settings, 'smtp_user', '')
                raw_to = getattr(settings, 'email_to', '') or getattr(settings, 'smtp_to', '')
                to_addrs = [addr.strip() for addr in raw_to.split(',') if addr.strip()]
                if deliver_email(
                    session, brief,
                    smtp_host=smtp_host,
                    smtp_port=getattr(settings, 'smtp_port', 587),
                    smtp_user=getattr(settings, 'smtp_user', ''),
                    smtp_pass=getattr(settings, 'smtp_pass', ''),
                    from_addr=from_addr,
                    to_addrs=to_addrs,
                ):
                    console.print("[green]✓ Email delivered[/green]")
                else:
                    console.print("[red]✗ Email failed[/red]")
            else:
                console.print("[yellow]⚠ Email not configured (set SMTP_HOST, SMTP_USER, etc.)[/yellow]")

        if not telegram and not email:
            console.print("[yellow]Specify --telegram and/or --email to deliver[/yellow]")

    finally:
        session.close()


@brief_app.command("list")
def brief_list():
    """List generated briefs."""
    from src.db.session import get_session
    from src.db.models import Briefs

    session = get_session()
    try:
        briefs = session.query(Briefs).order_by(Briefs.created_at.desc()).limit(10).all()
        if not briefs:
            console.print("[dim]No briefs generated yet[/dim]")
            return

        for b in briefs:
            status_color = "green" if b.status == "published" else "yellow"
            tg = "✓" if b.delivered_telegram else "—"
            em = "✓" if b.delivered_email else "—"
            console.print(
                f"[{status_color}]{b.status:>10}[/{status_color}] "
                f"{b.title}  "
                f"[dim]TG:{tg} Email:{em} Claims:{len(b.claim_ids or [])}[/dim]"
            )
    finally:
        session.close()
