"""CLI commands for managing tracked people."""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from src.db.models import People
from src.db.session import get_session

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("list")
def list_people(
    tier: Optional[int] = typer.Option(None, help="Filter by tier (1-3)"),
    active_only: bool = typer.Option(True, "--active/--all", help="Show only active"),
):
    """List all tracked people."""
    session = get_session()
    try:
        query = session.query(People)
        if tier:
            query = query.filter(People.tier == tier)
        if active_only:
            query = query.filter(People.active == True)  # noqa: E712
        query = query.order_by(People.tier, People.name)
        people = query.all()

        table = Table(title="Tracked People", show_lines=True)
        table.add_column("ID", style="dim", max_width=8)
        table.add_column("Name", style="bold")
        table.add_column("Domain")
        table.add_column("Tier", justify="center")
        table.add_column("Expertise", max_width=40)
        table.add_column("Active", justify="center")

        for p in people:
            expertise = ", ".join(p.expertise_domains or [])
            table.add_row(
                str(p.id)[:8],
                p.name,
                p.domain or "",
                str(p.tier),
                expertise,
                "✓" if p.active else "✗",
            )

        console.print(table)
        console.print(f"\nTotal: {len(people)} people")
    finally:
        session.close()


@app.command("add")
def add_person(
    name: str = typer.Option(..., prompt=True, help="Display name"),
    domain: str = typer.Option("", prompt=True, help="Domain (e.g., 'Macro / Principles')"),
    tier: int = typer.Option(..., prompt=True, help="Tier (1-3)"),
    inclusion_notes: str = typer.Option(..., prompt=True, help="Why this person is tracked"),
    expertise: str = typer.Option("", prompt="Expertise domains (comma-separated)", help="Expertise domains"),
    search_queries: str = typer.Option("", prompt="YouTube search queries (comma-separated)", help="Search queries"),
):
    """Add a new person to track."""
    session = get_session()
    try:
        expertise_list = [e.strip() for e in expertise.split(",") if e.strip()] if expertise else []
        query_list = [q.strip() for q in search_queries.split(",") if q.strip()] if search_queries else []

        person = People(
            name=name,
            domain=domain or None,
            tier=tier,
            inclusion_notes=inclusion_notes,
            expertise_domains=expertise_list,
            youtube_search_queries=query_list,
        )
        session.add(person)
        session.commit()
        console.print(f"\n[bold green]✓ Added:[/bold green] {name} (tier {tier}, id: {str(person.id)[:8]})")
    except Exception as e:
        session.rollback()
        console.print(f"\n[bold red]✗ Error:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


@app.command("edit")
def edit_person(
    person_id: str = typer.Argument(help="Person ID (can be partial)"),
    name: Optional[str] = typer.Option(None, help="New name"),
    domain: Optional[str] = typer.Option(None, help="New domain"),
    tier: Optional[int] = typer.Option(None, help="New tier (1-3)"),
    inclusion_notes: Optional[str] = typer.Option(None, help="New inclusion notes"),
    expertise: Optional[str] = typer.Option(None, help="New expertise domains (comma-separated)"),
    search_queries: Optional[str] = typer.Option(None, help="New YouTube search queries (comma-separated)"),
    active: Optional[bool] = typer.Option(None, "--active/--inactive", help="Set active state"),
):
    """Edit a tracked person."""
    session = get_session()
    try:
        person = _find_person(session, person_id)
        if not person:
            console.print(f"[bold red]✗ Person not found:[/bold red] {person_id}")
            raise typer.Exit(1)

        if name is not None:
            person.name = name
        if domain is not None:
            person.domain = domain
        if tier is not None:
            person.tier = tier
        if inclusion_notes is not None:
            person.inclusion_notes = inclusion_notes
        if expertise is not None:
            person.expertise_domains = [e.strip() for e in expertise.split(",") if e.strip()]
        if search_queries is not None:
            person.youtube_search_queries = [q.strip() for q in search_queries.split(",") if q.strip()]
        if active is not None:
            person.active = active

        session.commit()
        console.print(f"[bold green]✓ Updated:[/bold green] {person.name}")
    except typer.Exit:
        raise
    except Exception as e:
        session.rollback()
        console.print(f"[bold red]✗ Error:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


@app.command("remove")
def remove_person(
    person_id: str = typer.Argument(help="Person ID (can be partial)"),
):
    """Soft-delete a tracked person (set active=false)."""
    session = get_session()
    try:
        person = _find_person(session, person_id)
        if not person:
            console.print(f"[bold red]✗ Person not found:[/bold red] {person_id}")
            raise typer.Exit(1)

        person.active = False
        session.commit()
        console.print(f"[bold green]✓ Removed (soft-delete):[/bold green] {person.name}")
    except typer.Exit:
        raise
    except Exception as e:
        session.rollback()
        console.print(f"[bold red]✗ Error:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


@app.command("import")
def import_people(
    file: Path = typer.Argument(help="Path to JSON file with people data"),
):
    """Bulk import people from a JSON file."""
    if not file.exists():
        console.print(f"[bold red]✗ File not found:[/bold red] {file}")
        raise typer.Exit(1)

    with open(file) as f:
        data = json.load(f)

    session = get_session()
    try:
        count = 0
        for item in data:
            existing = session.query(People).filter(People.name == item["name"]).first()
            if existing:
                console.print(f"  [dim]Skipping (exists):[/dim] {item['name']}")
                continue

            person = People(
                name=item["name"],
                domain=item.get("domain"),
                tier=item["tier"],
                inclusion_notes=item["inclusion_notes"],
                expertise_domains=item.get("expertise_domains", []),
                youtube_search_queries=item.get("youtube_search_queries", []),
            )
            session.add(person)
            count += 1

        session.commit()
        console.print(f"\n[bold green]✓ Imported {count} people[/bold green] (from {len(data)} entries)")
    except Exception as e:
        session.rollback()
        console.print(f"\n[bold red]✗ Import failed:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


def _find_person(session, person_id: str) -> People | None:
    """Find a person by full or partial UUID."""
    # Try exact match first
    try:
        from uuid import UUID
        uid = UUID(person_id)
        return session.query(People).filter(People.id == uid).first()
    except ValueError:
        pass

    # Partial match — cast UUID to text and use LIKE
    from sqlalchemy import cast, Text as SAText
    results = (
        session.query(People)
        .filter(cast(People.id, SAText).like(f"{person_id}%"))
        .all()
    )
    if len(results) == 1:
        return results[0]
    return None
