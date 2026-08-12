import os
import subprocess
import sys

from typer.testing import CliRunner

from app.cli.main import app
from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task

runner = CliRunner()


def test_real_repository_filters_and_cli_are_read_only() -> None:
    with SessionLocal() as session:
        project = Project(name="CRM Project")
        session.add(project)
        session.flush()
        company = Company(project_id=project.id, name="CRM Company")
        session.add(company)
        session.flush()
        contact = Contact(
            company_id=company.id,
            first_name="Ada",
            last_name="Lovelace",
            job_title="Founder",
            email="ada@example.com",
            source="MANUAL_VERIFIED",
        )
        session.add(contact)
        session.flush()
        lead = Lead(company_id=company.id, contact_id=contact.id, status="NEW")
        session.add(lead)
        session.flush()
        session.add(Task(lead_id=lead.id, title="Review", status="TODO"))
        session.commit()
        project_id, company_id = project.id, company.id
        before = {
            table.__tablename__: session.query(table).count()
            for table in (Project, Company, Contact, Lead, Task)
        }

    result = runner.invoke(
        app,
        ["crm", "list", "--project-id", str(project_id), "--company-id", str(company_id)],
    )
    assert result.exit_code == 0
    assert "CRM Company" in result.output
    assert "Ada Lovelace" in result.output
    assert "NO_DRAFT" in result.output

    with SessionLocal() as session:
        after = {
            table.__tablename__: session.query(table).count()
            for table in (Project, Company, Contact, Lead, Task)
        }
    assert after == before


def test_crm_help_is_fresh_process_import_safe() -> None:
    proof = r"""
import smtplib
import socket
import pydantic_settings
import sqlalchemy
import sqlalchemy.orm
from typer.testing import CliRunner

counts = {name: 0 for name in ('settings', 'engine', 'sessionmaker', 'session', 'smtp', 'network')}
def blocked(name):
    def fail(*args, **kwargs):
        counts[name] += 1
        raise AssertionError(name)
    return fail
pydantic_settings.BaseSettings.__init__ = blocked('settings')
sqlalchemy.create_engine = blocked('engine')
sqlalchemy.orm.sessionmaker = blocked('sessionmaker')
sqlalchemy.orm.Session.__init__ = blocked('session')
smtplib.SMTP = blocked('smtp')
smtplib.SMTP_SSL = blocked('smtp')
socket.create_connection = blocked('network')
socket.getaddrinfo = blocked('network')
from app.cli.main import app
result = CliRunner().invoke(app, ['crm', '--help'])
print(result.exit_code)
print(counts)
"""
    environment = os.environ.copy()
    environment["DEBUG"] = "false"
    result = subprocess.run(
        [sys.executable, "-c", proof],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "0",
        "{'settings': 0, 'engine': 0, 'sessionmaker': 0, 'session': 0, 'smtp': 0, 'network': 0}",
    ]
