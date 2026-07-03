import typer

from app.cli.project import app as project_app

app = typer.Typer(
    help="Bali Leads Platform CLI",
)

app.add_typer(
    project_app,
    name="project",
)

if __name__ == "__main__":
    app()
