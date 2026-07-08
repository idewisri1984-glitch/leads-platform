from datetime import datetime

import typer

from app.core.database.session import SessionLocal
from app.modules.task import TaskCreate, TaskRepository, TaskService

app = typer.Typer(help="Task management commands.")


@app.command("create")
def create_task(
    lead_id: int,
    title: str,
    description: str = "",
    status: str = "TODO",
    due_at: str = "",
) -> None:
    parsed_due_at: datetime | None = None

    if due_at:
        try:
            parsed_due_at = datetime.fromisoformat(due_at)
        except ValueError as error:
            typer.secho(
                "Invalid due_at. Use an ISO datetime, for example 2026-07-08T14:30:00.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1) from error

    with SessionLocal() as session:
        repository = TaskRepository(session)
        service = TaskService(repository)
        task = service.create(
            TaskCreate(
                lead_id=lead_id,
                title=title,
                description=description or None,
                status=status,
                due_at=parsed_due_at,
            )
        )

    typer.secho("Task created", fg=typer.colors.GREEN)
    typer.echo(f"ID: {task.id}")
    typer.echo(f"Lead ID: {task.lead_id}")
    typer.echo(f"Title: {task.title}")
    typer.echo(f"Status: {task.status}")
    typer.echo(f"Due at: {task.due_at}")


@app.command("list")
def list_tasks() -> None:
    with SessionLocal() as session:
        repository = TaskRepository(session)
        service = TaskService(repository)
        tasks = service.get_all()

    if not tasks:
        typer.echo("No tasks found.")
        return

    typer.echo("\nTasks\n")

    for task in tasks:
        due_at = str(task.due_at) if task.due_at is not None else "-"
        typer.echo(f"{task.id:>3}  {task.lead_id:>3}  {task.title}  {task.status}  {due_at}")


@app.command("show")
def show_task(task_id: int) -> None:
    with SessionLocal() as session:
        repository = TaskRepository(session)
        service = TaskService(repository)
        task = service.get(task_id)

    if task is None:
        typer.secho("Task not found.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo()
    typer.echo(f"ID:          {task.id}")
    typer.echo(f"Lead ID:     {task.lead_id}")
    typer.echo(f"Title:       {task.title}")
    typer.echo(f"Description: {task.description}")
    typer.echo(f"Status:      {task.status}")
    typer.echo(f"Due at:      {task.due_at}")


@app.command("delete")
def delete_task(task_id: int) -> None:
    with SessionLocal() as session:
        repository = TaskRepository(session)
        service = TaskService(repository)
        task = repository.get(task_id)

        if task is None:
            typer.secho("Task not found.", fg=typer.colors.RED)
            raise typer.Exit(1)

        service.delete(task)

    typer.secho("Task deleted", fg=typer.colors.GREEN)
