# -*- coding: utf-8 -*-

import json
import logging
import os

import pytest

from dvc.exceptions import DvcException
from dvc.exceptions import NoMetricsError
from dvc.main import main
from dvc.repo import Repo as DvcRepo
from dvc.repo.metrics.show import NO_METRICS_FILE_AT_REFERENCE_WARNING
from dvc.utils import relpath
from tests.basic_env import TestDvcGit


class TestMetricsBase(TestDvcGit):
    def setUp(self):
        super().setUp()
        self.dvc.scm.commit("init")

        branches = ["foo", "bar", "baz"]

        for branch in branches:
            self.dvc.scm.repo.create_head(branch)

        for branch in branches:
            self.dvc.scm.checkout(branch)

            self.create("metric", branch)
            self.create("metric_json", json.dumps({"branch": branch}))
            self.create("metric_csv", branch)
            self.create("metric_hcsv", "branch\n" + branch)
            self.create("metric_tsv", branch)
            self.create("metric_htsv", "branch\n" + branch)

            if branch == "foo":
                deviation_mse_train = 0.173461
            else:
                deviation_mse_train = 0.356245

            self.create(
                "metric_json_ext",
                json.dumps(
                    {
                        "metrics": [
                            {
                                "dataset": "train",
                                "deviation_mse": deviation_mse_train,
                                "value_mse": 0.421601,
                            },
                            {
                                "dataset": "testing",
                                "deviation_mse": 0.289545,
                                "value_mse": 0.297848,
                            },
                            {
                                "dataset": "validation",
                                "deviation_mse": 0.67528,
                                "value_mse": 0.671502,
                            },
                        ]
                    }
                ),
            )

            files = [
                "metric",
                "metric_json",
                "metric_tsv",
                "metric_htsv",
                "metric_csv",
                "metric_hcsv",
                "metric_json_ext",
            ]

            self.dvc.run(metrics_no_cache=files, overwrite=True)

            self.dvc.scm.add(files + ["metric.dvc"])

            self.dvc.scm.commit("metric")

        self.dvc.scm.checkout("master")


def test_show_dirty(tmp_dir, scm, dvc):
    tmp_dir.gen("metric", "master")
    dvc.run(metrics_no_cache=["metric"], overwrite=True)
    tmp_dir.scm_add(["metric", "metric.dvc"], commit="add metric")

    tmp_dir.gen("metric", "dirty")

    assert dvc.metrics.show(["metric"]) == {"": {"metric": "dirty"}}

    assert dvc.metrics.show(["metric"], all_branches=True) == {
        "working tree": {"metric": "dirty"},
        "master": {"metric": "master"},
    }

    assert dvc.metrics.show(["metric"], all_tags=True) == {
        "working tree": {"metric": "dirty"}
    }


class TestMetrics(TestMetricsBase):
    def test_show(self):
        ret = self.dvc.metrics.show(["metric"], all_branches=True)
        self.assertEqual(len(ret), 3)
        self.assertEqual(ret["foo"]["metric"], "foo")
        self.assertEqual(ret["bar"]["metric"], "bar")
        self.assertEqual(ret["baz"]["metric"], "baz")

        ret = self.dvc.metrics.show(
            ["metric_json"], typ="json", xpath="branch", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        self.assertSequenceEqual(ret["foo"]["metric_json"], {"branch": "foo"})
        self.assertSequenceEqual(ret["bar"]["metric_json"], {"branch": "bar"})
        self.assertSequenceEqual(ret["baz"]["metric_json"], {"branch": "baz"})

        ret = self.dvc.metrics.show(
            ["metric_tsv"], typ="tsv", xpath="0,0", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        self.assertSequenceEqual(ret["foo"]["metric_tsv"], ["foo"])
        self.assertSequenceEqual(ret["bar"]["metric_tsv"], ["bar"])
        self.assertSequenceEqual(ret["baz"]["metric_tsv"], ["baz"])

        ret = self.dvc.metrics.show(
            ["metric_htsv"], typ="htsv", xpath="0,branch", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        self.assertSequenceEqual(ret["foo"]["metric_htsv"], ["foo"])
        self.assertSequenceEqual(ret["bar"]["metric_htsv"], ["bar"])
        self.assertSequenceEqual(ret["baz"]["metric_htsv"], ["baz"])

        ret = self.dvc.metrics.show(
            ["metric_csv"], typ="csv", xpath="0,0", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        self.assertSequenceEqual(ret["foo"]["metric_csv"], ["foo"])
        self.assertSequenceEqual(ret["bar"]["metric_csv"], ["bar"])
        self.assertSequenceEqual(ret["baz"]["metric_csv"], ["baz"])

        ret = self.dvc.metrics.show(
            ["metric_hcsv"], typ="hcsv", xpath="0,branch", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        self.assertSequenceEqual(ret["foo"]["metric_hcsv"], ["foo"])
        self.assertSequenceEqual(ret["bar"]["metric_hcsv"], ["bar"])
        self.assertSequenceEqual(ret["baz"]["metric_hcsv"], ["baz"])

        ret = self.dvc.metrics.show(
            ["metric_json_ext"],
            typ="json",
            xpath="$.metrics[?(@.deviation_mse<0.30) & (@.value_mse>0.4)]",
            all_branches=True,
        )
        self.assertEqual(len(ret), 1)
        self.assertSequenceEqual(
            ret["foo"]["metric_json_ext"],
            {
                "metrics.[0]": {
                    "dataset": "train",
                    "deviation_mse": 0.173461,
                    "value_mse": 0.421601,
                }
            },
        )
        self.assertRaises(KeyError, lambda: ret["bar"])
        self.assertRaises(KeyError, lambda: ret["baz"])

    def test_unknown_type_ignored(self):
        ret = self.dvc.metrics.show(
            ["metric_hcsv"], typ="unknown", xpath="0,branch", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        for b in ["foo", "bar", "baz"]:
            self.assertEqual(ret[b]["metric_hcsv"].split(), ["branch", b])

    def test_type_case_normalized(self):
        ret = self.dvc.metrics.show(
            ["metric_hcsv"], typ=" hCSV ", xpath="0,branch", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        for b in ["foo", "bar", "baz"]:
            self.assertSequenceEqual(ret[b]["metric_hcsv"], [b])

    def test_xpath_is_empty(self):
        ret = self.dvc.metrics.show(
            ["metric_json"], typ="json", xpath="", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        for b in ["foo", "bar", "baz"]:
            self.assertEqual(ret[b]["metric_json"], json.dumps({"branch": b}))

    def test_xpath_is_none(self):
        ret = self.dvc.metrics.show(
            ["metric_json"], typ="json", xpath=None, all_branches=True
        )
        self.assertEqual(len(ret), 3)
        for b in ["foo", "bar", "baz"]:
            self.assertEqual(ret[b]["metric_json"], json.dumps({"branch": b}))

    def test_xpath_all_columns(self):
        ret = self.dvc.metrics.show(
            ["metric_hcsv"], typ="hcsv ", xpath="0,", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        for b in ["foo", "bar", "baz"]:
            self.assertSequenceEqual(ret[b]["metric_hcsv"], [b])

    def test_xpath_all_rows(self):
        ret = self.dvc.metrics.show(
            ["metric_csv"], typ="csv", xpath=",0", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        for b in ["foo", "bar", "baz"]:
            self.assertSequenceEqual(ret[b]["metric_csv"], [b])

    def test_xpath_all(self):
        ret = self.dvc.metrics.show(
            ["metric_csv"], typ="csv", xpath=",", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        for b in ["foo", "bar", "baz"]:
            self.assertSequenceEqual(ret[b]["metric_csv"], [[b]])

    def test_xpath_all_with_header(self):
        ret = self.dvc.metrics.show(
            ["metric_hcsv"], typ="hcsv", xpath=",", all_branches=True
        )
        self.assertEqual(len(ret), 3)
        for b in ["foo", "bar", "baz"]:
            self.assertSequenceEqual(ret[b]["metric_hcsv"], [[b]])

    def test_formatted_output(self):
        # Labels are in Spanish to test UTF-8
        self.create(
            "metrics.csv",
            (
                "valor_mse,desviación_mse,data_set\n"
                "0.421601,0.173461,entrenamiento\n"
                "0.67528,0.289545,pruebas\n"
                "0.671502,0.297848,validación\n"
            ),
        )

        # Contains quoted newlines to test output correctness
        self.create(
            "metrics.tsv",
            (
                "value_mse\tdeviation_mse\tdata_set\n"
                "0.421601\t0.173461\ttrain\n"
                '0.67528\t0.289545\t"test\\ning"\n'
                "0.671502\t0.297848\tvalidation\n"
            ),
        )

        self.create(
            "metrics.json",
            (
                "{\n"
                '     "data_set": [\n'
                '          "train",\n'
                '          "testing",\n'
                '          "validation"\n'
                "     ],\n"
                '     "deviation_mse": [\n'
                '          "0.173461",\n'
                '          "0.289545",\n'
                '          "0.297848"\n'
                "     ],\n"
                '     "value_mse": [\n'
                '          "0.421601",\n'
                '          "0.67528",\n'
                '          "0.671502"\n'
                "     ]\n"
                "}"
            ),
        )

        self.create(
            "metrics.txt", "ROC_AUC: 0.64\nKS: 78.9999999996\nF_SCORE: 77\n"
        )

        self.dvc.run(
            fname="testing_metrics_output.dvc",
            metrics_no_cache=[
                "metrics.csv",
                "metrics.tsv",
                "metrics.json",
                "metrics.txt",
            ],
        )

        self.dvc.metrics.modify("metrics.csv", typ="csv")
        self.dvc.metrics.modify("metrics.tsv", typ="tsv")
        self.dvc.metrics.modify("metrics.json", typ="json")

        self._caplog.clear()

        with self._caplog.at_level(logging.INFO, logger="dvc"):
            ret = main(["metrics", "show"])
            self.assertEqual(ret, 0)

        expected_csv = (
            "\tmetrics.csv:\n"
            "\t\tvalor_mse   desviación_mse   data_set       \n"
            "\t\t0.421601    0.173461         entrenamiento  \n"
            "\t\t0.67528     0.289545         pruebas        \n"
            "\t\t0.671502    0.297848         validación"
        )

        expected_tsv = (
            "\tmetrics.tsv:\n"
            "\t\tvalue_mse   deviation_mse   data_set    \n"
            "\t\t0.421601    0.173461        train       \n"
            "\t\t0.67528     0.289545        test\\ning   \n"
            "\t\t0.671502    0.297848        validation"
        )

        expected_txt = (
            "\tmetrics.txt:\n"
            "\t\tROC_AUC: 0.64\n"
            "\t\tKS: 78.9999999996\n"
            "\t\tF_SCORE: 77"
        )

        expected_json = (
            "\tmetrics.json:\n"
            "\t\t{\n"
            '\t\t     "data_set": [\n'
            '\t\t          "train",\n'
            '\t\t          "testing",\n'
            '\t\t          "validation"\n'
            "\t\t     ],\n"
            '\t\t     "deviation_mse": [\n'
            '\t\t          "0.173461",\n'
            '\t\t          "0.289545",\n'
            '\t\t          "0.297848"\n'
            "\t\t     ],\n"
            '\t\t     "value_mse": [\n'
            '\t\t          "0.421601",\n'
            '\t\t          "0.67528",\n'
            '\t\t          "0.671502"\n'
            "\t\t     ]\n"
            "\t\t}"
        )

        stdout = "\n".join(record.message for record in self._caplog.records)

        assert expected_tsv in stdout
        assert expected_csv in stdout
        assert expected_txt in stdout
        assert expected_json in stdout

    def test_show_all_should_be_current_dir_agnostic(self):
        os.chdir(self.DATA_DIR)

        metrics = self.dvc.metrics.show(all_branches=True)
        self.assertMetricsHaveRelativePaths(metrics)

    def assertMetricsHaveRelativePaths(self, metrics):
        root_relpath = relpath(self.dvc.root_dir)
        metric_path = os.path.join(root_relpath, "metric")
        metric_json_path = os.path.join(root_relpath, "metric_json")
        metric_tsv_path = os.path.join(root_relpath, "metric_tsv")
        metric_htsv_path = os.path.join(root_relpath, "metric_htsv")
        metric_csv_path = os.path.join(root_relpath, "metric_csv")
        metric_hcsv_path = os.path.join(root_relpath, "metric_hcsv")
        metric_json_ext_path = os.path.join(root_relpath, "metric_json_ext")
        for branch in ["bar", "baz", "foo"]:
            self.assertEqual(
                set(metrics[branch].keys()),
                {
                    metric_path,
                    metric_json_path,
                    metric_tsv_path,
                    metric_htsv_path,
                    metric_csv_path,
                    metric_hcsv_path,
                    metric_json_ext_path,
                },
            )


class TestMetricsRecursive(TestDvcGit):
    def setUp(self):
        super().setUp()
        self.dvc.scm.commit("init")

        self.dvc.scm.checkout("nested", create_new=True)

        os.mkdir("nested")
        os.mkdir(os.path.join("nested", "subnested"))

        ret = main(
            [
                "run",
                "-M",
                os.path.join("nested", "metric_nested"),
                "echo",
                "nested",
                ">>",
                os.path.join("nested", "metric_nested"),
            ]
        )

        self.assertEqual(ret, 0)

        ret = main(
            [
                "run",
                "-M",
                os.path.join("nested", "subnested", "metric_subnested"),
                "echo",
                "subnested",
                ">>",
                os.path.join("nested", "subnested", "metric_subnested"),
            ]
        )

        self.assertEqual(ret, 0)

        self.dvc.scm.add(
            ["nested", "metric_nested.dvc", "metric_subnested.dvc"]
        )
        self.dvc.scm.commit("nested metrics")

        self.dvc.scm.checkout("master")

    def test(self):
        ret = self.dvc.metrics.show(
            ["nested"], all_branches=True, recursive=False
        )
        self.assertEqual(len(ret), 1)

        ret = self.dvc.metrics.show(
            ["nested"], all_branches=True, recursive=True
        )
        self.assertEqual(len(ret), 1)
        self.assertEqual(
            ret["nested"][
                os.path.join("nested", "subnested", "metric_subnested")
            ],
            "subnested",
        )
        self.assertEqual(
            ret["nested"][os.path.join("nested", "metric_nested")], "nested"
        )


class TestMetricsReproCLI(TestDvcGit):
    def test(self):
        stage = self.dvc.run(
            metrics_no_cache=["metrics"],
            cmd="python {} {} {}".format(self.CODE, self.FOO, "metrics"),
        )

        ret = main(["repro", "-m", stage.path])
        self.assertEqual(ret, 0)

        ret = main(["metrics", "remove", "metrics"])
        self.assertEqual(ret, 0)

        ret = main(["repro", "-f", "-m", stage.path])
        self.assertNotEqual(ret, 0)

        ret = main(["metrics", "add", "metrics"])
        self.assertEqual(ret, 0)

        ret = main(["metrics", "modify", "-t", "CSV", "-x", "0,0", "metrics"])
        self.assertEqual(ret, 0)

        ret = main(["repro", "-f", "-m", stage.path])
        self.assertEqual(ret, 0)

    def test_dir(self):
        os.mkdir("metrics_dir")

        with self.assertRaises(DvcException):
            self.dvc.run(metrics_no_cache=["metrics_dir"])

    def test_binary(self):
        with open("metrics_bin", "w+") as fd:
            fd.write("\0\0\0\0\0\0\0\0")

        with self.assertRaises(DvcException):
            self.dvc.run(metrics_no_cache=["metrics_bin"])


class TestMetricsCLI(TestMetricsBase):
    def test(self):
        # FIXME check output
        ret = main(["metrics", "show", "-a", "metric", "-v"])
        self.assertEqual(ret, 0)

        ret = main(
            [
                "metrics",
                "show",
                "-a",
                "metric_json",
                "-t",
                "json",
                "-x",
                "branch",
            ]
        )
        self.assertEqual(ret, 0)
        ret = main(
            ["metrics", "show", "-a", "metric_tsv", "-t", "tsv", "-x", "0,0"]
        )
        self.assertEqual(ret, 0)
        ret = main(
            [
                "metrics",
                "show",
                "-a",
                "metric_htsv",
                "-t",
                "htsv",
                "-x",
                "0,branch",
            ]
        )
        self.assertEqual(ret, 0)

        ret = main(
            ["metrics", "show", "-a", "metric_csv", "-t", "csv", "-x", "0,0"]
        )
        self.assertEqual(ret, 0)

        ret = main(
            [
                "metrics",
                "show",
                "-a",
                "metric_hcsv",
                "-t",
                "hcsv",
                "-x",
                "0,branch",
            ]
        )
        self.assertEqual(ret, 0)

    def test_dir(self):
        os.mkdir("metrics_dir")

        with self.assertRaises(DvcException):
            self.dvc.run(outs_no_cache=["metrics_dir"])
            self.dvc.metrics.add("metrics_dir")

    def test_binary(self):
        with open("metrics_bin", "w+") as fd:
            fd.write("\0\0\0\0\0\0\0\0")

        with self.assertRaises(DvcException):
            self.dvc.run(outs_no_cache=["metrics_bin"])
            self.dvc.metrics.add("metrics_bin")

    def test_non_existing(self):
        ret = main(["metrics", "add", "non-existing"])
        self.assertNotEqual(ret, 0)

        ret = main(["metrics", "modify", "non-existing"])
        self.assertNotEqual(ret, 0)

        ret = main(["metrics", "remove", "non-existing"])
        self.assertNotEqual(ret, 0)

    def test_wrong_type_add(self):
        with open("metric.unknown", "w+") as fd:
            fd.write("unknown")
            fd.flush()

        ret = main(["add", "metric.unknown"])
        assert ret == 0

        self._caplog.clear()
        ret = main(["metrics", "add", "metric.unknown", "-t", "unknown"])
        assert ret == 1

        assert (
            "failed to add metric file 'metric.unknown'"
        ) in self._caplog.text

        assert (
            "'unknown' is not supported, must be one of "
            "[raw, json, csv, tsv, hcsv, htsv]"
        ) in self._caplog.text

        ret = main(["metrics", "add", "metric.unknown", "-t", "raw"])
        assert ret == 0

        self._caplog.clear()
        ret = main(["metrics", "show", "metric.unknown"])
        assert ret == 0

        assert "\tmetric.unknown: unknown" in self._caplog.text

    def test_wrong_type_modify(self):
        with open("metric.unknown", "w+") as fd:
            fd.write("unknown")
            fd.flush()

        ret = main(["run", "-m", "metric.unknown"])
        assert ret == 0

        self._caplog.clear()

        ret = main(["metrics", "modify", "metric.unknown", "-t", "unknown"])
        assert ret == 1

        assert "failed to modify metric file settings" in self._caplog.text

        assert (
            "metric type 'unknown' is not supported, must be one of "
            "[raw, json, csv, tsv, hcsv, htsv]"
        ) in self._caplog.text

        ret = main(["metrics", "modify", "metric.unknown", "-t", "CSV"])
        assert ret == 0

        self._caplog.clear()

        ret = main(["metrics", "show", "metric.unknown"])
        assert ret == 0

        assert "\tmetric.unknown: unknown" in self._caplog.text

    def test_wrong_type_show(self):
        with open("metric.unknown", "w+") as fd:
            fd.write("unknown")
            fd.flush()

        ret = main(["run", "-m", "metric.unknown"])
        assert ret == 0

        self._caplog.clear()

        ret = main(
            ["metrics", "show", "metric.unknown", "-t", "unknown", "-x", "0,0"]
        )
        assert ret == 0
        assert "\tmetric.unknown: unknown" in self._caplog.text


class TestNoMetrics(TestDvcGit):
    def test(self):
        with self.assertRaises(NoMetricsError):
            self.dvc.metrics.show()

    def test_cli(self):
        ret = main(["metrics", "show"])
        self.assertNotEqual(ret, 0)


class TestCachedMetrics(TestDvcGit):
    def _do_add(self, branch):
        self.dvc.scm.checkout(branch)
        self.dvc.checkout(force=True)
        assert not os.path.exists("metrics.json")

        with open("metrics.json", "w+") as fd:
            json.dump({"metrics": branch}, fd)

        stages = self.dvc.add("metrics.json")
        self.dvc.metrics.add("metrics.json", typ="json", xpath="metrics")
        self.assertEqual(len(stages), 1)
        stage = stages[0]
        self.assertIsNotNone(stage)

        self.dvc.scm.add([".gitignore", "metrics.json.dvc"])
        self.dvc.scm.commit(branch)

    def _do_run(self, branch):
        self.dvc.scm.checkout(branch)
        self.dvc.checkout(force=True)

        with open("code.py", "w+") as fobj:
            fobj.write("import sys\n")
            fobj.write("import os\n")
            fobj.write("import json\n")
            fobj.write(
                'print(json.dumps({{"metrics": "{branch}"}}))\n'.format(
                    branch=branch
                )
            )

        stage = self.dvc.run(
            deps=["code.py"],
            metrics=["metrics.json"],
            cmd="python code.py metrics.json > metrics.json",
        )
        self.assertIsNotNone(stage)
        self.assertEqual(stage.relpath, "metrics.json.dvc")

        self.dvc.scm.add(["code.py", ".gitignore", "metrics.json.dvc"])
        self.dvc.scm.commit(branch)

    def _test_metrics(self, func):
        self.dvc.scm.commit("init")

        self.dvc.scm.branch("one")
        self.dvc.scm.branch("two")

        func("master")
        func("one")
        func("two")

        # TestDvc currently is based on TestGit, so it is safe to use
        # scm.git for now
        self.dvc.scm.repo.git.clean("-fd")

        self.dvc = DvcRepo(".")

        res = self.dvc.metrics.show(
            ["metrics.json"], all_branches=True, typ="json", xpath="metrics"
        )

        self.assertEqual(
            res,
            {
                "master": {"metrics.json": {"metrics": "master"}},
                "one": {"metrics.json": {"metrics": "one"}},
                "two": {"metrics.json": {"metrics": "two"}},
            },
        )

        res = self.dvc.metrics.show(
            all_branches=True, typ="json", xpath="metrics"
        )

        self.assertEqual(
            res,
            {
                "master": {"metrics.json": {"metrics": "master"}},
                "one": {"metrics.json": {"metrics": "one"}},
                "two": {"metrics.json": {"metrics": "two"}},
            },
        )

    def test_add(self):
        self._test_metrics(self._do_add)

    def test_run(self):
        self._test_metrics(self._do_run)


class TestMetricsType(TestDvcGit):
    branches = ["foo", "bar", "baz"]
    files = [
        "metric",
        "metric.txt",
        "metric.json",
        "metric.tsv",
        "metric.htsv",
        "metric.csv",
        "metric.hcsv",
    ]
    xpaths = [None, None, "branch", "0,0", "0,branch", "0,0", "0,branch"]

    def setUp(self):
        super().setUp()
        self.dvc.scm.commit("init")

        for branch in self.branches:
            self.dvc.scm.checkout(branch, create_new=True)
            with open("metric", "w+") as fd:
                fd.write(branch)
            with open("metric.txt", "w+") as fd:
                fd.write(branch)
            with open("metric.json", "w+") as fd:
                json.dump({"branch": branch}, fd)
            with open("metric.csv", "w+") as fd:
                fd.write(branch)
            with open("metric.hcsv", "w+") as fd:
                fd.write("branch\n")
                fd.write(branch)
            with open("metric.tsv", "w+") as fd:
                fd.write(branch)
            with open("metric.htsv", "w+") as fd:
                fd.write("branch\n")
                fd.write(branch)
            self.dvc.run(metrics_no_cache=self.files, overwrite=True)
            self.dvc.scm.add(self.files + ["metric.dvc"])
            self.dvc.scm.commit("metric")

        self.dvc.scm.checkout("master")

    def test_show(self):
        for file_name, xpath in zip(self.files, self.xpaths):
            self._do_show(file_name, xpath)

    def _do_show(self, file_name, xpath):
        ret = self.dvc.metrics.show(
            [file_name], xpath=xpath, all_branches=True
        )
        self.assertEqual(len(ret), 3)
        for branch in self.branches:
            if isinstance(ret[branch][file_name], list):
                self.assertSequenceEqual(ret[branch][file_name], [branch])
            elif isinstance(ret[branch][file_name], dict):
                self.assertSequenceEqual(
                    ret[branch][file_name], {"branch": branch}
                )
            else:
                self.assertSequenceEqual(ret[branch][file_name], branch)


def test_display_missing_metrics(tmp_dir, scm, dvc, caplog):
    scm.branch("branch")

    # Create a metric in master
    tmp_dir.gen("metric", "0.5")
    assert 0 == main(["run", "-m", "metric"])
    tmp_dir.scm_add("metric.dvc", commit="master commit")

    # Create a metric in branch
    scm.checkout("branch")
    tmp_dir.gen("metric", "0.5")
    assert 0 == main(["run", "-M", "metric"])
    tmp_dir.scm_add("metric.dvc", commit="branch commit")

    os.remove("metric")
    assert 0 == main(["metrics", "show", "-a"])
    assert (
        NO_METRICS_FILE_AT_REFERENCE_WARNING.format("metric", "branch")
        in caplog.text
    )


def test_show_xpath_should_override_stage_xpath(tmp_dir, dvc):
    tmp_dir.gen("metric", json.dumps({"m1": 0.1, "m2": 0.2}))

    dvc.run(cmd="", overwrite=True, metrics=["metric"])
    dvc.metrics.modify("metric", typ="json", xpath="m2")

    assert dvc.metrics.show(xpath="m1") == {"": {"metric": {"m1": 0.1}}}


def test_show_multiple_outputs(tmp_dir, dvc, caplog):
    tmp_dir.gen(
        {
            "1.json": json.dumps({"AUC": 1}),
            "2.json": json.dumps({"AUC": 2}),
            "metrics/3.json": json.dumps({"AUC": 3}),
        }
    )

    dvc.run(cmd="", overwrite=True, metrics=["1.json"])
    dvc.run(cmd="", overwrite=True, metrics=["2.json"])
    dvc.run(cmd="", overwrite=True, metrics=["metrics/3.json"])

    with caplog.at_level(logging.INFO, logger="dvc"):
        assert 0 == main(["metrics", "show", "1.json", "2.json"])
        assert '1.json: {"AUC": 1}' in caplog.text
        assert '2.json: {"AUC": 2}' in caplog.text

    caplog.clear()

    with caplog.at_level(logging.INFO, logger="dvc"):
        assert 0 == main(["metrics", "show", "-R", "1.json", "metrics"])
        assert '1.json: {"AUC": 1}' in caplog.text
        assert '3.json: {"AUC": 3}' in caplog.text

    caplog.clear()

    with caplog.at_level(logging.INFO, logger="dvc"):
        assert 1 == main(["metrics", "show", "1.json", "not-found"])
        assert '1.json: {"AUC": 1}' in caplog.text
        assert (
            "the following metrics do not exist, "
            "are not metric files or are malformed: 'not-found'"
        ) in caplog.text


def test_metrics_diff_raw(tmp_dir, scm, dvc):
    def _gen(val):
        tmp_dir.gen({"metrics": val})
        dvc.run(cmd="", metrics=["metrics"])
        dvc.scm.add(["metrics.dvc"])
        dvc.scm.commit(str(val))

    _gen("raw 1")
    _gen("raw 2")
    _gen("raw 3")

    assert dvc.metrics.diff(a_rev="HEAD~2") == {
        "metrics": {"": {"old": "raw 1", "new": "raw 3"}}
    }


@pytest.mark.parametrize("xpath", [True, False])
def test_metrics_diff_json(tmp_dir, scm, dvc, xpath):
    def _gen(val):
        metrics = {"a": {"b": {"c": val, "d": 1, "e": str(val)}}}
        tmp_dir.gen({"m.json": json.dumps(metrics)})
        dvc.run(cmd="", metrics=["m.json"])
        dvc.metrics.modify("m.json", typ="json")
        if xpath:
            dvc.metrics.modify("m.json", xpath="a.b.c")
        dvc.scm.add(["m.json.dvc"])
        dvc.scm.commit(str(val))

    _gen(1)
    _gen(2)
    _gen(3)

    expected = {"m.json": {"a.b.c": {"old": 1, "new": 3, "diff": 2}}}

    if not xpath:
        expected["m.json"]["a.b.e"] = {"old": "1", "new": "3"}

    assert expected == dvc.metrics.diff(a_rev="HEAD~2")


def test_metrics_diff_broken_json(tmp_dir, scm, dvc):
    metrics = {"a": {"b": {"c": 1, "d": 1, "e": "3"}}}
    tmp_dir.gen({"m.json": json.dumps(metrics)})
    dvc.run(cmd="", metrics_no_cache=["m.json"])
    dvc.scm.add(["m.json.dvc", "m.json"])
    dvc.scm.commit("add metrics")

    (tmp_dir / "m.json").write_text(json.dumps(metrics) + "ma\nlformed\n")

    assert dvc.metrics.diff() == {
        "m.json": {
            "a.b.c": {"old": 1, "new": "unable to parse"},
            "a.b.d": {"old": 1, "new": "unable to parse"},
            "a.b.e": {"old": "3", "new": "unable to parse"},
        }
    }


def test_metrics_diff_no_metrics(tmp_dir, scm, dvc):
    tmp_dir.scm_gen({"foo": "foo"}, commit="add foo")
    assert dvc.metrics.diff(a_rev="HEAD~1") == {}


def test_metrics_diff_new_metric(tmp_dir, scm, dvc):
    metrics = {"a": {"b": {"c": 1, "d": 1, "e": "3"}}}
    tmp_dir.gen({"m.json": json.dumps(metrics)})
    dvc.run(cmd="", metrics_no_cache=["m.json"])

    assert dvc.metrics.diff() == {
        "m.json": {
            "a.b.c": {"old": None, "new": 1},
            "a.b.d": {"old": None, "new": 1},
            "a.b.e": {"old": None, "new": "3"},
        }
    }


def test_metrics_diff_deleted_metric(tmp_dir, scm, dvc):
    metrics = {"a": {"b": {"c": 1, "d": 1, "e": "3"}}}
    tmp_dir.gen({"m.json": json.dumps(metrics)})
    dvc.run(cmd="", metrics_no_cache=["m.json"])
    dvc.scm.add(["m.json.dvc", "m.json"])
    dvc.scm.commit("add metrics")

    (tmp_dir / "m.json").unlink()

    assert dvc.metrics.diff() == {
        "m.json": {
            "a.b.c": {"old": 1, "new": None},
            "a.b.d": {"old": 1, "new": None},
            "a.b.e": {"old": "3", "new": None},
        }
    }
