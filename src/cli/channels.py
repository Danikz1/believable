"""CLI commands for managing podcast channels and channel roles."""

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from src.db.models import ChannelRoles, People, PodcastChannels
from src.db.session import get_session

app = typer.Typer(no_args_is_help=True)
console = Console()

# ---- Channel sub-commands ----


@app.command("list")
def list_channels(
    tier: Optional[int] = typer.Option(None, help="Filter by tier (1-3)"),
    active_only: bool = typer.Option(True, "--active/--all", help="Show only active"),
):
    """List all podcast channels."""
    session = get_session()
    try:
        query = session.query(PodcastChannels)
        if tier:
            query = query.filter(PodcastChannels.tier == tier)
        if active_only:
            query = query.filter(PodcastChannels.active == True)  # noqa: E712
        query = query.order_by(PodcastChannels.tier, PodcastChannels.name)
        channels = query.all()

        table = Table(title="Podcast Channels", show_lines=True)
        table.add_column("ID", style="dim", max_width=8)
        table.add_column("Name", style="bold")
        table.add_column("Tier", justify="center")
        table.add_column("Mode")
        table.add_column("YouTube ID", max_width=30)
        table.add_column("Transcript", max_width=18)
        table.add_column("Active", justify="center")

        for ch in channels:
            table.add_row(
                str(ch.id)[:8],
                ch.name,
                str(ch.tier),
                ch.monitoring_mode,
                ch.youtube_channel_id[:30] if ch.youtube_channel_id else "",
                ch.transcript_parser or "",
                "✓" if ch.active else "✗",
            )

        console.print(table)
        console.print(f"\nTotal: {len(channels)} channels")
    finally:
        session.close()


@app.command("add")
def add_channel(
    name: str = typer.Option(..., prompt=True, help="Channel name"),
    youtube_channel_id: str = typer.Option(..., prompt=True, help="YouTube channel ID"),
    tier: int = typer.Option(..., prompt=True, help="Tier (1-3)"),
    monitoring_mode: str = typer.Option("channel_feed", help="'channel_feed' or 'search_gap_fill'"),
    uploads_playlist_id: Optional[str] = typer.Option(None, help="Uploads playlist ID"),
    transcript_url_pattern: Optional[str] = typer.Option(None, help="Official transcript URL pattern"),
    transcript_parser: Optional[str] = typer.Option(None, help="Official transcript parser name"),
):
    """Add a new podcast channel."""
    session = get_session()
    try:
        channel = PodcastChannels(
            youtube_channel_id=youtube_channel_id,
            name=name,
            tier=tier,
            monitoring_mode=monitoring_mode,
            uploads_playlist_id=uploads_playlist_id,
            transcript_url_pattern=transcript_url_pattern,
            transcript_parser=transcript_parser,
        )
        session.add(channel)
        session.commit()
        console.print(f"\n[bold green]✓ Added:[/bold green] {name} (tier {tier})")
    except Exception as e:
        session.rollback()
        console.print(f"\n[bold red]✗ Error:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


@app.command("edit")
def edit_channel(
    channel_id: str = typer.Argument(help="Channel ID (partial OK)"),
    name: Optional[str] = typer.Option(None, help="New name"),
    youtube_channel_id: Optional[str] = typer.Option(None, help="New YouTube channel ID"),
    tier: Optional[int] = typer.Option(None, help="New tier"),
    monitoring_mode: Optional[str] = typer.Option(None, help="New monitoring mode"),
    uploads_playlist_id: Optional[str] = typer.Option(None, help="New uploads playlist ID"),
    transcript_url_pattern: Optional[str] = typer.Option(None, help="New official transcript URL pattern"),
    transcript_parser: Optional[str] = typer.Option(None, help="New official transcript parser"),
    active: Optional[bool] = typer.Option(None, "--active/--inactive", help="Set active state"),
):
    """Edit a podcast channel."""
    session = get_session()
    try:
        channel = _find_channel(session, channel_id)
        if not channel:
            console.print(f"[bold red]✗ Channel not found:[/bold red] {channel_id}")
            raise typer.Exit(1)

        if name is not None:
            channel.name = name
        if youtube_channel_id is not None:
            channel.youtube_channel_id = youtube_channel_id
        if tier is not None:
            channel.tier = tier
        if monitoring_mode is not None:
            channel.monitoring_mode = monitoring_mode
        if uploads_playlist_id is not None:
            channel.uploads_playlist_id = uploads_playlist_id
        if transcript_url_pattern is not None:
            channel.transcript_url_pattern = transcript_url_pattern
        if transcript_parser is not None:
            channel.transcript_parser = transcript_parser
        if active is not None:
            channel.active = active

        session.commit()
        console.print(f"[bold green]✓ Updated:[/bold green] {channel.name}")
    except typer.Exit:
        raise
    except Exception as e:
        session.rollback()
        console.print(f"[bold red]✗ Error:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


@app.command("remove")
def remove_channel(
    channel_id: str = typer.Argument(help="Channel ID (partial OK)"),
):
    """Soft-delete a podcast channel."""
    session = get_session()
    try:
        channel = _find_channel(session, channel_id)
        if not channel:
            console.print(f"[bold red]✗ Channel not found:[/bold red] {channel_id}")
            raise typer.Exit(1)

        channel.active = False
        session.commit()
        console.print(f"[bold green]✓ Removed:[/bold green] {channel.name}")
    except typer.Exit:
        raise
    except Exception as e:
        session.rollback()
        console.print(f"[bold red]✗ Error:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


# ---- Roles sub-commands ----

roles_app = typer.Typer(no_args_is_help=True)
app.add_typer(roles_app, name="roles", help="Manage channel roles (host/cohost/guest)")


@roles_app.command("list")
def list_roles(
    channel_name: Optional[str] = typer.Option(None, "--channel", help="Filter by channel name"),
):
    """List channel roles (host/cohost/guest mappings)."""
    session = get_session()
    try:
        query = (
            session.query(ChannelRoles)
            .join(PodcastChannels, ChannelRoles.channel_id == PodcastChannels.id)
            .join(People, ChannelRoles.person_id == People.id)
        )
        if channel_name:
            query = query.filter(PodcastChannels.name.ilike(f"%{channel_name}%"))

        roles = query.all()

        table = Table(title="Channel Roles", show_lines=True)
        table.add_column("Channel", style="bold")
        table.add_column("Person")
        table.add_column("Role")

        for r in roles:
            table.add_row(r.channel.name, r.person.name, r.role)

        console.print(table)
        console.print(f"\nTotal: {len(roles)} roles")
    finally:
        session.close()


@roles_app.command("add")
def add_role(
    channel_name: str = typer.Option(..., prompt=True, help="Channel name"),
    person_name: str = typer.Option(..., prompt=True, help="Person name"),
    role: str = typer.Option(..., prompt=True, help="Role: host / cohost / frequent_guest"),
):
    """Add a channel role."""
    session = get_session()
    try:
        channel = session.query(PodcastChannels).filter(PodcastChannels.name == channel_name).first()
        person = session.query(People).filter(People.name == person_name).first()

        if not channel:
            console.print(f"[bold red]✗ Channel not found:[/bold red] {channel_name}")
            raise typer.Exit(1)
        if not person:
            console.print(f"[bold red]✗ Person not found:[/bold red] {person_name}")
            raise typer.Exit(1)

        cr = ChannelRoles(channel_id=channel.id, person_id=person.id, role=role)
        session.add(cr)
        session.commit()
        console.print(f"[bold green]✓ Added:[/bold green] {person_name} → {channel_name} ({role})")
    except typer.Exit:
        raise
    except Exception as e:
        session.rollback()
        console.print(f"[bold red]✗ Error:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


@roles_app.command("remove")
def remove_role(
    channel_name: str = typer.Option(..., prompt=True, help="Channel name"),
    person_name: str = typer.Option(..., prompt=True, help="Person name"),
    role: str = typer.Option(..., prompt=True, help="Role to remove"),
):
    """Remove a channel role."""
    session = get_session()
    try:
        channel = session.query(PodcastChannels).filter(PodcastChannels.name == channel_name).first()
        person = session.query(People).filter(People.name == person_name).first()

        if not channel or not person:
            console.print("[bold red]✗ Channel or person not found[/bold red]")
            raise typer.Exit(1)

        cr = (
            session.query(ChannelRoles)
            .filter(
                ChannelRoles.channel_id == channel.id,
                ChannelRoles.person_id == person.id,
                ChannelRoles.role == role,
            )
            .first()
        )
        if not cr:
            console.print("[bold red]✗ Role not found[/bold red]")
            raise typer.Exit(1)

        session.delete(cr)
        session.commit()
        console.print(f"[bold green]✓ Removed:[/bold green] {person_name} from {channel_name} ({role})")
    except typer.Exit:
        raise
    except Exception as e:
        session.rollback()
        console.print(f"[bold red]✗ Error:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


def _find_channel(session, channel_id: str) -> PodcastChannels | None:
    """Find a channel by full or partial UUID."""
    try:
        from uuid import UUID
        uid = UUID(channel_id)
        return session.query(PodcastChannels).filter(PodcastChannels.id == uid).first()
    except ValueError:
        pass
    # Partial match by name
    return session.query(PodcastChannels).filter(PodcastChannels.name.ilike(f"%{channel_id}%")).first()
