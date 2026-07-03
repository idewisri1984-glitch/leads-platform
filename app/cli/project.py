import typer

from app.core.database.session import SessionLocal
from app.modules.project import (
    ProjectCreate,
    ProjectRepository,
    ProjectService,
)

app = typer.Typer(help="Project management commands.")


@app.command("create")
def create_project(name: str) -> None:
    """
    Create a new project.
    """
    with SessionLocal() as session:
        repository = ProjectRepository(session)
        service = ProjectService(repository)

        project = service.create(
            ProjectCreate(
                name=name,
            )
        )

    typer.secho("✔ Project created", fg=typer.colors.GREEN)
    typer.echo(f"ID: {project.id}")
    typer.echo(f"Name: {project.name}")


@app.command("list")
def list_projects() -> None:
    """
    List all projects.
    """
    with SessionLocal() as session:
        repository = ProjectRepository(session)
        service = ProjectService(repository)

        projects = service.get_all()

    if not projects:
        typer.echo("No projects found.")
        return

    typer.echo("\nProjects\n")

    for project in projects:
        typer.echo(f"{project.id:>3}  {project.name}")
