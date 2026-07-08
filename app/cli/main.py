import typer

from app.cli.company import app as company_app
from app.cli.contact import app as contact_app
from app.cli.lead import app as lead_app
from app.cli.project import app as project_app
from app.cli.task import app as task_app

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

app.add_typer(
    contact_app,
    name="contact",
)

app.add_typer(
    lead_app,
    name="lead",
)

app.add_typer(
    task_app,
    name="task",
)

if __name__ == "__main__":
    app()
