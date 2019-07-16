import logging

from dvc.cli import parse_args
from dvc.exceptions import DvcException
from dvc.command.imp_url import CmdImportUrl


def test_import_url(mocker, dvc_repo):
    cli_args = parse_args(["import-url", "src", "out", "--file", "file"])
    assert cli_args.func == CmdImportUrl

    cmd = cli_args.func(cli_args)
    m = mocker.patch.object(cmd.repo, "imp_url", autospec=True)

    assert cmd.run() == 0

    m.assert_called_once_with("src", out="out", fname="file")


def test_failed_import_url(mocker, caplog, dvc_repo):
    cli_args = parse_args(["import-url", "http://somesite.com/file_name"])
    assert cli_args.func == CmdImportUrl

    cmd = cli_args.func(cli_args)
    with mocker.patch.object(
        cmd.repo, "imp_url", side_effect=DvcException("error")
    ):
        with caplog.at_level(logging.ERROR, logger="dvc"):
            assert cmd.run() == 1
            expected_error = (
                "failed to import http://somesite.com/file_name. "
                "You could also try downloading it manually and "
                "adding it with `dvc add` command."
            )
            assert expected_error in caplog.text
