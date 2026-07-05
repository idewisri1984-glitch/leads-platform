import typer

from app.cli.company import app as company_app
from app.cli.project import app as project_app

app = typer.Typer(
    help="Bali Leads Platform CLI",
)

app.add_typer(
    project_app,
    name="project",
)

app.add_typer(
    company_app,
    name="company",
)

if __name__ == "__main__":
    app()
