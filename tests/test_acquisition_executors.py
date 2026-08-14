from importlib import import_module


def test_executor_modules_import_without_cli_reverse_dependency() -> None:
    modules = (
        import_module("app.modules.agent.execution"),
        import_module("app.modules.email_draft.execution"),
        import_module("app.modules.crm.export_execution"),
    )

    assert all(not module.__name__.startswith("app.cli") for module in modules)


def test_agent_cli_uses_application_executor_functions() -> None:
    agent = import_module("app.cli.agent")

    assert agent.execute_company_plan.__module__ == "app.modules.agent.execution"
    assert agent.execute_company_apply.__module__ == "app.modules.agent.execution"
    assert agent.execute_contact_plan.__module__ == "app.modules.agent.execution"
    assert agent.execute_contact_apply.__module__ == "app.modules.agent.execution"


def test_email_and_crm_executors_are_application_level() -> None:
    drafts = import_module("app.modules.email_draft.execution")
    crm = import_module("app.modules.crm.export_execution")

    assert drafts.execute_email_draft_generation.__module__ == drafts.__name__
    assert crm.execute_crm_excel_export.__module__ == crm.__name__
