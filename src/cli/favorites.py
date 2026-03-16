"""Favorites CLI — manage tracked people and channels."""

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="favorites", help="Manage favorite people and channels")
console = Console()


@app.command("list")
def list_favorites():
    """Show all favorites."""
    from src.db.models import Favorites
    from src.db.session import get_session

    session = get_session()
    favs = session.query(Favorites).order_by(Favorites.priority).all()

    table = Table(title="Favorites")
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Priority", justify="right")
    table.add_column("Notify")
    table.add_column("ID", style="dim")

    for f in favs:
        ftype = "👤 Person" if f.person_id else "📺 Channel"
        name = f.person.name if f.person else (f.channel.name if f.channel else "?")
        table.add_row(
            ftype, name, str(f.priority),
            "✓" if f.notify else "✗",
            str(f.id)[:8],
        )

    console.print(table)
    session.close()


@app.command("add")
def add_favorite(
    entity_type: str = typer.Argument(..., help="'person' or 'channel'"),
    name: str = typer.Argument(..., help="Name to search for"),
    priority: int = typer.Option(5, "--priority", "-p", help="Priority 1-10"),
):
    """Add a person or channel as a favorite."""
    from src.db.models import Favorites, People, PodcastChannels
    from src.db.session import get_session

    session = get_session()
    try:
        if entity_type == "person":
            person = session.query(People).filter(People.name.ilike(f"%{name}%")).first()
            if not person:
                console.print(f"[red]Person '{name}' not found[/red]")
                raise typer.Exit(1)
            fav = Favorites(person_id=person.id, priority=priority)
            session.add(fav)
            session.commit()
            console.print(f"[green]✓ Added {person.name} as favorite (priority {priority})[/green]")
        elif entity_type == "channel":
            channel = session.query(PodcastChannels).filter(PodcastChannels.name.ilike(f"%{name}%")).first()
            if not channel:
                console.print(f"[red]Channel '{name}' not found[/red]")
                raise typer.Exit(1)
            fav = Favorites(channel_id=channel.id, priority=priority)
            session.add(fav)
            session.commit()
            console.print(f"[green]✓ Added {channel.name} as favorite (priority {priority})[/green]")
        else:
            console.print("[red]Type must be 'person' or 'channel'[/red]")
            raise typer.Exit(1)
    except Exception as e:
        session.rollback()
        console.print(f"[red]✗ Failed: {e}[/red]")
    finally:
        session.close()


@app.command("remove")
def remove_favorite(
    entity_type: str = typer.Argument(..., help="'person' or 'channel'"),
    name: str = typer.Argument(..., help="Name to search for"),
):
    """Remove a favorite."""
    from src.db.models import Favorites, People, PodcastChannels
    from src.db.session import get_session

    session = get_session()
    try:
        if entity_type == "person":
            person = session.query(People).filter(People.name.ilike(f"%{name}%")).first()
            if not person:
                console.print(f"[red]Person '{name}' not found[/red]")
                raise typer.Exit(1)
            fav = session.query(Favorites).filter(Favorites.person_id == person.id).first()
        elif entity_type == "channel":
            channel = session.query(PodcastChannels).filter(PodcastChannels.name.ilike(f"%{name}%")).first()
            if not channel:
                console.print(f"[red]Channel '{name}' not found[/red]")
                raise typer.Exit(1)
            fav = session.query(Favorites).filter(Favorites.channel_id == channel.id).first()
        else:
            console.print("[red]Type must be 'person' or 'channel'[/red]")
            raise typer.Exit(1)

        if not fav:
            console.print("[yellow]Not in favorites[/yellow]")
            raise typer.Exit(0)

        session.delete(fav)
        session.commit()
        console.print("[green]✓ Removed from favorites[/green]")
    finally:
        session.close()
