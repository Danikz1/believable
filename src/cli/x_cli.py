"""X/Twitter CLI — manually ingest posts and manage X integration."""

import typer
from rich.console import Console

app = typer.Typer(name="x", help="X/Twitter post ingestion")
console = Console()


@app.command("add")
def add_post(
    url: str = typer.Argument(..., help="X/Twitter post URL"),
    text: str = typer.Option(..., "--text", "-t", help="Post text content"),
    person: str = typer.Option(..., "--person", "-p", help="Person name to match"),
    thread: bool = typer.Option(False, "--thread", help="Mark as part of a thread"),
):
    """Add an X post manually (Option A: no API needed).

    Example:
        bm x add "https://x.com/RayDalio/status/123456" --text "Markets will..." --person "Ray Dalio"
    """
    from uuid import UUID

    from src.db.models import People
    from src.db.session import get_session
    from src.pipeline.x_ingestion import ingest_x_post

    session = get_session()
    try:
        # Find the person
        matched = session.query(People).filter(People.name.ilike(f"%{person}%")).first()
        if not matched:
            console.print(f"[red]Person '{person}' not found. Use 'bm people list' to see tracked people.[/red]")
            raise typer.Exit(1)

        console.print(f"[cyan]Ingesting post by {matched.name}...[/cyan]")
        result = ingest_x_post(
            url=url,
            text=text,
            person_id=matched.id,
            session=session,
            is_thread=thread,
        )

        status = result["status"]
        if status == "already_exists":
            console.print("[yellow]Post already ingested[/yellow]")
        elif status == "skipped":
            console.print(f"[yellow]Post skipped: {result.get('reason', 'not substantive')}[/yellow]")
        elif status == "enriched":
            console.print(f"[green]✓ Ingested — {result['claims_extracted']} claims extracted[/green]")
        else:
            console.print(f"[yellow]Post stored as pending (status: {status})[/yellow]")

    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
    except Exception as e:
        session.rollback()
        console.print(f"[red]✗ Error: {e}[/red]")
    finally:
        session.close()


@app.command("list")
def list_posts(
    limit: int = typer.Option(20, "--limit", "-n"),
    person: str = typer.Option(None, "--person", "-p", help="Filter by person name"),
):
    """List ingested X posts."""
    from rich.table import Table

    from src.db.models import People, XPosts
    from src.db.session import get_session

    session = get_session()
    q = session.query(XPosts).order_by(XPosts.created_at.desc())

    if person:
        matched = session.query(People).filter(People.name.ilike(f"%{person}%")).first()
        if matched:
            q = q.filter(XPosts.person_id == matched.id)
        else:
            console.print(f"[yellow]Person '{person}' not found[/yellow]")
            session.close()
            return

    posts = q.limit(limit).all()

    table = Table(title=f"X Posts ({len(posts)})")
    table.add_column("Person", style="cyan")
    table.add_column("Text", max_width=60)
    table.add_column("Status")
    table.add_column("Claims")
    table.add_column("Date", style="dim")

    for p in posts:
        name = p.person.name if p.person else "?"
        text_preview = p.post_text[:55] + "..." if len(p.post_text) > 55 else p.post_text
        claim_count = len(p.claims) if p.claims else 0
        date = p.posted_at.strftime("%b %d") if p.posted_at else "?"
        status_style = {"enriched": "green", "skipped": "dim", "pending": "yellow"}.get(p.status, "white")
        table.add_row(name, text_preview, f"[{status_style}]{p.status}[/{status_style}]", str(claim_count), date)

    console.print(table)
    session.close()


@app.command("enrich")
def enrich_pending():
    """Enrich all pending X posts (extract claims)."""
    from src.db.models import XPosts
    from src.db.session import get_session
    from src.pipeline.x_ingestion import _extract_claims_from_x

    session = get_session()
    pending = session.query(XPosts).filter(XPosts.status == "pending").all()

    if not pending:
        console.print("[green]No pending X posts[/green]")
        session.close()
        return

    console.print(f"[cyan]Enriching {len(pending)} pending posts...[/cyan]")
    total_claims = 0
    for post in pending:
        try:
            count = _extract_claims_from_x(post, session)
            post.status = "enriched"
            total_claims += count
            console.print(f"  {post.person.name if post.person else '?'}: {count} claims")
        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]")

    session.commit()
    console.print(f"[green]✓ {total_claims} total claims extracted[/green]")
    session.close()


@app.command("scan")
def scan_timelines(
    limit: int = typer.Option(10, "--limit", "-n", help="Max posts per person"),
):
    """Scan X timelines for tracked people with x_handles.

    NOTE: Requires X API credentials (TWITTER_BEARER_TOKEN env var).
    If not available, will print instructions for manual ingestion instead.
    """
    import os

    from src.db.models import People, XPosts
    from src.db.session import get_session

    bearer = os.getenv("TWITTER_BEARER_TOKEN")

    session = get_session()
    people = session.query(People).filter(
        People.x_handle.isnot(None),
        People.active == True,
    ).all()

    if not people:
        console.print("[yellow]No tracked people have x_handle set.[/yellow]")
        console.print("Set handles with: bm people edit <name> --x-handle <handle>")
        session.close()
        return

    console.print(f"[cyan]Found {len(people)} people with X handles[/cyan]")

    if not bearer:
        console.print("\n[yellow]⚠  TWITTER_BEARER_TOKEN not set — API scanning unavailable.[/yellow]")
        console.print("[dim]To scan automatically, set the env var and re-run.[/dim]")
        console.print("\n[bold]Manual ingestion instructions:[/bold]")
        for p in people:
            console.print(f"  Visit https://x.com/{p.x_handle} and use:")
            console.print(f'    bm x add "<url>" --text "<text>" --person "{p.name}"')
        session.close()
        return

    # API-based scanning
    import httpx

    headers = {"Authorization": f"Bearer {bearer}"}
    total_new = 0

    for person in people:
        console.print(f"\n[cyan]Scanning @{person.x_handle} ({person.name})...[/cyan]")
        try:
            # Get user ID from handle
            user_resp = httpx.get(
                f"https://api.twitter.com/2/users/by/username/{person.x_handle}",
                headers=headers,
            )
            if user_resp.status_code != 200:
                console.print(f"  [red]API error: {user_resp.status_code}[/red]")
                continue
            user_data = user_resp.json().get("data", {})
            user_id = user_data.get("id")
            if not user_id:
                console.print(f"  [yellow]User not found[/yellow]")
                continue

            # Get recent tweets
            tweets_resp = httpx.get(
                f"https://api.twitter.com/2/users/{user_id}/tweets",
                headers=headers,
                params={
                    "max_results": min(limit, 100),
                    "tweet.fields": "created_at,text",
                    "exclude": "retweets,replies",
                },
            )
            if tweets_resp.status_code != 200:
                console.print(f"  [red]Tweets API error: {tweets_resp.status_code}[/red]")
                continue

            tweets = tweets_resp.json().get("data", [])
            new_count = 0
            for tweet in tweets:
                tweet_id = tweet["id"]
                # Skip if already ingested
                existing = session.query(XPosts).filter(
                    XPosts.platform_post_id == tweet_id
                ).first()
                if existing:
                    continue

                from src.pipeline.x_ingestion import ingest_x_post
                from datetime import datetime

                posted_at = None
                if tweet.get("created_at"):
                    try:
                        posted_at = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
                    except Exception:
                        pass

                result = ingest_x_post(
                    url=f"https://x.com/{person.x_handle}/status/{tweet_id}",
                    text=tweet["text"],
                    person_id=person.id,
                    session=session,
                    posted_at=posted_at,
                )
                if result["status"] == "enriched":
                    new_count += 1
                    total_new += 1

            console.print(f"  {new_count} new posts ingested from {len(tweets)} scanned")

        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]")

    console.print(f"\n[green]✓ Scan complete: {total_new} new posts ingested[/green]")
    session.close()


@app.command("status")
def x_status():
    """Show X ingestion stats."""
    from src.db.models import XPosts
    from src.db.session import get_session

    session = get_session()
    total = session.query(XPosts).count()
    enriched = session.query(XPosts).filter(XPosts.status == "enriched").count()
    pending = session.query(XPosts).filter(XPosts.status == "pending").count()
    skipped = session.query(XPosts).filter(XPosts.status == "skipped").count()

    console.print(f"\n[bold]X/Twitter Ingestion Status[/bold]")
    console.print(f"  Total posts: {total}")
    console.print(f"  [green]Enriched: {enriched}[/green]")
    console.print(f"  [yellow]Pending: {pending}[/yellow]")
    console.print(f"  [dim]Skipped: {skipped}[/dim]")
    session.close()
