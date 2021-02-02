from copy import deepcopy
from textwrap import dedent

import pytest
from funcy import first

from dvc import stage as stage_module
from dvc.exceptions import MetricsError

LIVE_SCRITP = dedent(
    """
        import dvclive
        import sys
        r = 2
        for i in range(r):
           dvclive.log("loss", 1-i/r)
           dvclive.log("accuracy", i/r)
           dvclive.next_step()"""
)


@pytest.fixture
def live_stage(tmp_dir, scm, dvc):

    pytest.skip("dvclive does not exist yet")

    def make(summary=True, html=True):
        tmp_dir.gen("train.py", LIVE_SCRITP)
        tmp_dir.gen("params.yaml", "foo: 1")
        stage = dvc.run(
            cmd="python train.py",
            params=["foo"],
            deps=["train.py"],
            name="live_stage",
            live="logs",
            live_no_summary=not summary,
            live_no_html=not html,
        )

        scm.add(["dvc.yaml", "dvc.lock", "train.py", "params.yaml"])
        scm.commit("initial: live_stage")
        return stage

    yield make


@pytest.mark.parametrize("report", (True, False))
@pytest.mark.parametrize("summary", (True, False))
def test_export_config_tmp(tmp_dir, dvc, mocker, summary, report):
    run_spy = mocker.spy(stage_module.run, "_run")
    tmp_dir.gen("src", "dependency")
    dvc.run(
        cmd="mkdir logs && touch logs.json",
        deps=["src"],
        name="run_logger",
        live="logs",
        live_no_summary=not summary,
        live_no_html=not report,
    )

    assert run_spy.call_count == 1
    _, kwargs = run_spy.call_args

    assert "DVCLIVE_PATH" in kwargs["env"]
    assert kwargs["env"]["DVCLIVE_PATH"] == "logs"

    assert "DVCLIVE_SUMMARY" in kwargs["env"]
    assert kwargs["env"]["DVCLIVE_SUMMARY"] == str(int(summary))

    assert "DVCLIVE_HTML" in kwargs["env"]
    assert kwargs["env"]["DVCLIVE_HTML"] == str(int(report))


@pytest.mark.parametrize("summary", (True, False))
def test_export_config(tmp_dir, dvc, mocker, summary, live_stage):
    run_spy = mocker.spy(stage_module.run, "_run")
    live_stage(summary=summary)

    assert run_spy.call_count == 1
    _, kwargs = run_spy.call_args

    assert "DVCLIVE_PATH" in kwargs["env"]
    assert kwargs["env"]["DVCLIVE_PATH"] == "logs"

    assert "DVCLIVE_SUMMARY" in kwargs["env"]
    assert kwargs["env"]["DVCLIVE_SUMMARY"] == str(int(summary))


def test_live_provides_metrics(tmp_dir, dvc, live_stage):
    live_stage(summary=True)

    assert (tmp_dir / "logs.json").is_file()
    assert dvc.metrics.show() == {
        "": {"logs.json": {"step": 1, "loss": 0.5, "accuracy": 0.5}}
    }

    assert (tmp_dir / "logs").is_dir()
    plots = dvc.plots.show()
    assert "logs/accuracy.tsv" in plots
    assert "logs/loss.tsv" in plots


def test_live_provides_no_metrics(tmp_dir, dvc, live_stage):
    live_stage(summary=False)

    assert not (tmp_dir / "logs.json").is_file()
    with pytest.raises(MetricsError):
        assert dvc.metrics.show() == {}

    assert (tmp_dir / "logs").is_dir()
    plots = dvc.plots.show()
    assert "logs/accuracy.tsv" in plots
    assert "logs/loss.tsv" in plots


def test_experiments_track_summary(tmp_dir, scm, dvc, live_stage):
    live_stage(summary=True)
    baseline_rev = scm.get_rev()

    experiments = dvc.experiments.run(targets=["live_stage"], params=["foo=2"])
    assert len(experiments) == 1
    ((exp_rev, _),) = experiments.items()

    res = dvc.experiments.show()
    assert "logs.json" in res[baseline_rev][exp_rev]["metrics"].keys()


@pytest.mark.parametrize("html", [True, False])
def test_live_html(tmp_dir, dvc, live_stage, html):
    live_stage(html=html)

    assert (tmp_dir / "logs.html").is_file() == html


@pytest.fixture
def live_checkpoint_stage(tmp_dir, scm, dvc):

    pytest.skip("dvclive does not exist yet")

    SCRIPT = dedent(
        """
            import os
            import dvclive

            def read(path):
                value=0
                if os.path.exists(path):
                    with open(path, 'r') as fobj:
                        try:
                            value = int(fobj.read())
                        except ValueError:
                            pass
                return value

            def dump(value, path):
                with open(path, "w") as fobj:
                    fobj.write(str(value))

            r = 3
            checkpoint_file = "checkpoint"

            value = read(checkpoint_file)
            for i in range(1,r):
                m = i + value
                dump(m, checkpoint_file)

                dvclive.log("metric1", m)
                dvclive.log("metric2", m * 2)
                dvclive.next_step()"""
    )

    tmp_dir.gen("train.py", SCRIPT)
    tmp_dir.gen("params.yaml", "foo: 1")
    stage = dvc.run(
        cmd="python train.py",
        params=["foo"],
        deps=["train.py"],
        name="live_stage",
        live="logs",
        checkpoints=["checkpoint"],
        no_exec=True,
    )

    scm.add(["dvc.yaml", "train.py", "params.yaml", ".gitignore"])
    scm.commit("initial: live_stage")
    yield stage


def checkpoints_metric(show_results, metric_file, metric_name):
    tmp = deepcopy(show_results)
    tmp.pop("workspace")
    tmp = first(tmp.values())
    tmp.pop("baseline")
    return list(
        map(
            lambda exp: exp["metrics"][metric_file][metric_name],
            list(tmp.values()),
        )
    )


def test_live_checkpoints_resume(tmp_dir, scm, dvc, live_checkpoint_stage):
    results = dvc.experiments.run(
        live_checkpoint_stage.addressing, params=["foo=2"], tmp_dir=False
    )

    checkpoint_resume = first(results)

    dvc.experiments.run(
        live_checkpoint_stage.addressing,
        checkpoint_resume=checkpoint_resume,
        tmp_dir=False,
    )

    results = dvc.experiments.show()
    assert checkpoints_metric(results, "logs.json", "step") == [
        3,
        3,
        2,
        1,
        1,
        0,
    ]
    assert checkpoints_metric(results, "logs.json", "metric1") == [
        4,
        4,
        3,
        2,
        2,
        1,
    ]
    assert checkpoints_metric(results, "logs.json", "metric2") == [
        8,
        8,
        6,
        4,
        4,
        2,
    ]
