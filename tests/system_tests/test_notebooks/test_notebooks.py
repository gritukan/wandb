import json
import os
import pathlib
import re
import subprocess
import sys
from unittest import mock

import pytest
import wandb
import wandb.jupyter
import wandb.sdk.lib.apikey
import wandb.util


def test_login_timeout(notebook, monkeypatch):
    monkeypatch.setattr(
        wandb.util, "prompt_choices", lambda x, input_timeout=None, jupyter=True: x[0]
    )
    monkeypatch.setattr(
        wandb.wandb_lib.apikey,
        "prompt_choices",
        lambda x, input_timeout=None, jupyter=True: x[0],
    )
    with notebook("login_timeout.ipynb") as nb:
        nb.execute_all()
        output = nb.cell_output_text(1)
        assert "W&B disabled due to login timeout" in output

        output = nb.cell_output(1)
        assert output[-1]["data"]["text/plain"] == "False"


def test_one_cell(notebook, run_id):
    with notebook("one_cell.ipynb") as nb:
        nb.execute_all()
        output = nb.cell_output_html(2)
        assert run_id in output


def test_magic(notebook):
    with notebook("magic.ipynb") as nb:
        nb.execute_all()
        iframes = 0
        text = ""
        for c, cell in enumerate(nb.cells):
            for i, out in enumerate(cell["outputs"]):
                print(f"CELL {c} output {i}: ", out)  # noqa: T201
                if not out.get("data", {}).get("text/html"):
                    continue
                if c == 0 and i == 0:
                    assert "display:none" in out
                text += out["data"]["text/html"]
            iframes += 1
        assert notebook.base_url in text
        assert iframes == 6


def test_notebook_exits(user, assets_path):
    nb_path = pathlib.Path("notebooks") / "ipython_exit.py"
    script_fname = assets_path(nb_path)
    bindir = os.path.dirname(sys.executable)
    ipython = os.path.join(bindir, "ipython")
    cmd = [ipython, script_fname]
    subprocess.check_call(cmd)


def test_notebook_metadata_jupyter(mocked_module, notebook):
    with mock.patch("ipykernel.connect.get_connection_file") as ipyconnect:
        ipyconnect.return_value = "kernel-12345.json"
        serverapp = mocked_module("jupyter_server.serverapp")
        serverapp.list_running_servers.return_value = [
            {"url": notebook.base_url, "notebook_dir": "/test"}
        ]
        with mock.patch.object(
            wandb.jupyter.requests,
            "get",
            lambda *args, **kwargs: mock.MagicMock(
                json=lambda: [
                    {
                        "kernel": {"id": "12345"},
                        "notebook": {
                            "name": "test.ipynb",
                            "path": "test.ipynb",
                        },
                    }
                ]
            ),
        ):
            meta = wandb.jupyter.notebook_metadata(False)
            assert meta == {"path": "test.ipynb", "root": "/test", "name": "test.ipynb"}


def test_notebook_metadata_no_servers(mocked_module):
    with mock.patch("ipykernel.connect.get_connection_file") as ipyconnect:
        ipyconnect.return_value = "kernel-12345.json"
        serverapp = mocked_module("jupyter_server.serverapp")
        serverapp.list_running_servers.return_value = []
        meta = wandb.jupyter.notebook_metadata(False)
        assert meta == {}


def test_notebook_metadata_colab(mocked_module):
    colab = mocked_module("google.colab")
    colab._message.blocking_request.return_value = {
        "ipynb": {"metadata": {"colab": {"name": "koalab.ipynb"}}}
    }
    with mock.patch.object(
        wandb.jupyter,
        "notebook_metadata_from_jupyter_servers_and_kernel_id",
        lambda *args, **kwargs: {
            "path": "colab.ipynb",
            "root": "/consent",
            "name": "colab.ipynb",
        },
    ):
        wandb.jupyter.notebook_metadata_from_jupyter_servers_and_kernel_id()
        meta = wandb.jupyter.notebook_metadata(False)
        assert meta == {
            "root": "/content",
            "path": "colab.ipynb",
            "name": "colab.ipynb",
        }


def test_notebook_metadata_kaggle(mocked_module):
    os.environ["KAGGLE_KERNEL_RUN_TYPE"] = "test"
    kaggle = mocked_module("kaggle_session")
    kaggle_client = mock.MagicMock()
    kaggle_client.get_exportable_ipynb.return_value = {
        "source": json.dumps({"metadata": {}, "cells": []})
    }
    kaggle.UserSessionClient.return_value = kaggle_client
    with mock.patch.object(
        wandb.jupyter,
        "notebook_metadata_from_jupyter_servers_and_kernel_id",
        lambda *args, **kwargs: {},
    ):
        meta = wandb.jupyter.notebook_metadata(False)
        assert meta == {
            "root": "/kaggle/working",
            "path": "kaggle.ipynb",
            "name": "kaggle.ipynb",
        }


def test_notebook_not_exists(mocked_ipython, user, capsys):
    with mock.patch.dict(os.environ, {"WANDB_NOTEBOOK_NAME": "fake.ipynb"}):
        run = wandb.init()
        _, err = capsys.readouterr()
        assert "WANDB_NOTEBOOK_NAME should be a path" in err
        run.finish()


def test_databricks_notebook_doesnt_hang_on_wandb_login(mocked_module):
    # test for WB-5264
    # when we try to call wandb.login(), should fail with no-tty
    with mock.patch.object(
        wandb.sdk.lib.apikey,
        "_is_databricks",
        return_value=True,
    ):
        with pytest.raises(wandb.UsageError, match="tty"):
            wandb.login()


def test_mocked_notebook_html_default(user, run_id, mocked_ipython):
    wandb.load_ipython_extension(mocked_ipython)
    mocked_ipython.register_magics.assert_called_with(wandb.jupyter.WandBMagics)
    with wandb.init(id=run_id) as run:
        run.log({"acc": 99, "loss": 0})
        run.finish()
    displayed_html = [args[0].strip() for args, _ in mocked_ipython.html.call_args_list]
    for i, html in enumerate(displayed_html):
        print(f"[{i}]: {html}")  # noqa: T201
    assert any(run_id in html for html in displayed_html)
    assert any("Run history:" in html for html in displayed_html)


def test_mocked_notebook_html_quiet(user, run_id, mocked_ipython):
    run = wandb.init(id=run_id, settings=wandb.Settings(quiet=True))
    run.log({"acc": 99, "loss": 0})
    run.finish()
    displayed_html = [args[0].strip() for args, _ in mocked_ipython.html.call_args_list]
    for i, html in enumerate(displayed_html):
        print(f"[{i}]: {html}")  # noqa: T201
    assert any(run_id in html for html in displayed_html)
    assert not any("Run history:" in html for html in displayed_html)


def test_mocked_notebook_run_display(user, mocked_ipython):
    with wandb.init() as run:
        run.display()
    displayed_html = [args[0].strip() for args, _ in mocked_ipython.html.call_args_list]
    for i, html in enumerate(displayed_html):
        print(f"[{i}]: {html}")  # noqa: T201
    assert any("<iframe" in html for html in displayed_html)


def test_mocked_notebook_magic(user, run_id, mocked_ipython):
    magic = wandb.jupyter.WandBMagics(None)
    s = wandb.Settings()
    s.update_from_env_vars(os.environ)
    basic_settings = {
        "api_key": user,
        "base_url": s.base_url,
        "run_id": run_id,
    }
    magic.wandb(
        "",
        """with wandb.init(settings=wandb.Settings(**{})):
        wandb.log({{"a": 1}})""".format(basic_settings),
    )
    displayed_html = [args[0].strip() for args, _ in mocked_ipython.html.call_args_list]
    for i, html in enumerate(displayed_html):
        print(f"[{i}]: {html}")  # noqa: T201
    assert wandb.jupyter.__IFrame is None
    assert any("<iframe" in html for html in displayed_html)
    run_uri = f"{user}/uncategorized/runs/{run_id}"
    magic.wandb(run_uri)
    displayed_html = [args[0].strip() for args, _ in mocked_ipython.html.call_args_list]
    for i, html in enumerate(displayed_html):
        print(f"[{i}]: {html}")  # noqa: T201
    assert any(f"{run_uri}?jupyter=true" in html for html in displayed_html)


def test_code_saving(notebook):
    with notebook("code_saving.ipynb", save_code=False) as nb:
        nb.execute_all()
        assert "Failed to detect the name of this notebook" in nb.all_output_text()

    # Let's make sure we warn the user if they lie to us.
    with notebook("code_saving.ipynb") as nb:
        os.remove("code_saving.ipynb")
        nb.execute_all()
        assert "WANDB_NOTEBOOK_NAME should be a path" in nb.all_output_text()


def test_notebook_creates_artifact_job(notebook):
    with notebook("one_cell_disable_git.ipynb") as nb:
        nb.execute_all()
        output = nb.cell_output_html(2)
    # get the run id from the url in the output
    regex_string = r'http:\/\/localhost:\d+\/[^\/]+\/[^\/]+\/runs\/([^\'"]+)'
    run_id = re.search(regex_string, str(output)).group(1)

    api = wandb.Api()
    user = os.environ["WANDB_USERNAME"]
    run = api.run(f"{user}/uncategorized/{run_id}")
    used_artifacts = run.used_artifacts()
    assert len(used_artifacts) == 1
    assert (
        used_artifacts[0].name
        == "job-source-uncategorized-one_cell_disable_git.ipynb:v0"
    )


def test_notebook_creates_repo_job(notebook):
    with notebook("one_cell_set_git.ipynb") as nb:
        nb.execute_all()
        output = nb.cell_output_html(2)
    # get the run id from the url in the output
    regex_string = r'http:\/\/localhost:\d+\/[^\/]+\/[^\/]+\/runs\/([^\'"]+)'
    run_id = re.search(regex_string, str(output)).group(1)

    api = wandb.Api()
    user = os.environ["WANDB_USERNAME"]
    run = api.run(f"{user}/uncategorized/{run_id}")
    used_artifacts = run.used_artifacts()
    assert len(used_artifacts) == 1
    assert used_artifacts[0].name == "job-test-test_one_cell_set_git.ipynb:v0"
