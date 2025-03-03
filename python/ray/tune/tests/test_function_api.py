import json
import os
import sys
import shutil
import tempfile
import unittest

import ray
from ray.train import CheckpointConfig
from ray.air.constants import TRAINING_ITERATION
from ray.rllib import _register_all

from ray import tune
from ray.tune.logger import NoopLogger
from ray.tune.execution.placement_groups import PlacementGroupFactory
from ray.tune.trainable.util import TrainableUtil
from ray.tune.trainable import (
    with_parameters,
    wrap_function,
    FuncCheckpointUtil,
    FunctionTrainable,
)
from ray.tune.result import DEFAULT_METRIC
from ray.tune.schedulers import ResourceChangingScheduler


def creator_generator(logdir):
    def logger_creator(config):
        return NoopLogger(config, logdir)

    return logger_creator


class FuncCheckpointUtilTest(unittest.TestCase):
    def setUp(self):
        self.logdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.logdir)

    def testEmptyCheckpoint(self):
        checkpoint_dir = FuncCheckpointUtil.mk_null_checkpoint_dir(self.logdir)
        assert FuncCheckpointUtil.is_null_checkpoint(checkpoint_dir)

    def testTempCheckpointDir(self):
        checkpoint_dir = FuncCheckpointUtil.mk_temp_checkpoint_dir(self.logdir)
        assert FuncCheckpointUtil.is_temp_checkpoint_dir(checkpoint_dir)

    def testConvertTempToPermanent(self):
        checkpoint_dir = FuncCheckpointUtil.mk_temp_checkpoint_dir(self.logdir)
        new_checkpoint_dir = FuncCheckpointUtil.create_perm_checkpoint(
            checkpoint_dir, self.logdir, step=4
        )
        assert new_checkpoint_dir == TrainableUtil.find_checkpoint_dir(
            new_checkpoint_dir
        )
        assert os.path.exists(new_checkpoint_dir)
        assert not FuncCheckpointUtil.is_temp_checkpoint_dir(new_checkpoint_dir)

        tmp_checkpoint_dir = FuncCheckpointUtil.mk_temp_checkpoint_dir(self.logdir)
        assert tmp_checkpoint_dir != new_checkpoint_dir


class FunctionCheckpointingTest(unittest.TestCase):
    def setUp(self):
        self.logdir = tempfile.mkdtemp()
        self.logger_creator = creator_generator(self.logdir)

    def tearDown(self):
        shutil.rmtree(self.logdir)

    def testCheckpointReuse(self):
        """Test that repeated save/restore never reuses same checkpoint dir."""

        def train(config, checkpoint_dir=None):
            if checkpoint_dir:
                count = sum(
                    "checkpoint-" in path for path in os.listdir(checkpoint_dir)
                )
                assert count == 1, os.listdir(checkpoint_dir)

            for step in range(20):
                with tune.checkpoint_dir(step=step) as checkpoint_dir:
                    path = os.path.join(checkpoint_dir, "checkpoint-{}".format(step))
                    open(path, "a").close()
                tune.report(test=step)

        wrapped = wrap_function(train)
        checkpoint = None
        for i in range(5):
            new_trainable = wrapped(logger_creator=self.logger_creator)
            if checkpoint:
                new_trainable.restore(checkpoint)
            for i in range(2):
                result = new_trainable.train()
            checkpoint = new_trainable.save()
            new_trainable.stop()
        assert result[TRAINING_ITERATION] == 10

    def testCheckpointReuseObject(self):
        """Test that repeated save/restore never reuses same checkpoint dir."""

        def train(config, checkpoint_dir=None):
            if checkpoint_dir:
                count = sum(
                    "checkpoint-" in path for path in os.listdir(checkpoint_dir)
                )
                assert count == 1, os.listdir(checkpoint_dir)

            for step in range(20):
                with tune.checkpoint_dir(step=step) as checkpoint_dir:
                    path = os.path.join(checkpoint_dir, "checkpoint-{}".format(step))
                    open(path, "a").close()
                tune.report(test=step)

        wrapped = wrap_function(train)
        checkpoint = None
        for i in range(5):
            new_trainable = wrapped(logger_creator=self.logger_creator)
            if checkpoint:
                new_trainable.restore_from_object(checkpoint)
            for i in range(2):
                result = new_trainable.train()
            checkpoint = new_trainable.save_to_object()
            new_trainable.stop()
        self.assertTrue(result[TRAINING_ITERATION] == 10)

    def testCheckpointReuseObjectWithoutTraining(self):
        """Test that repeated save/restore never reuses same checkpoint dir."""

        def train(config, checkpoint_dir=None):
            if checkpoint_dir:
                count = sum(
                    "checkpoint-" in path for path in os.listdir(checkpoint_dir)
                )
                assert count == 1, os.listdir(checkpoint_dir)

            for step in range(20):
                with tune.checkpoint_dir(step=step) as checkpoint_dir:
                    path = os.path.join(checkpoint_dir, "checkpoint-{}".format(step))
                    open(path, "a").close()
                tune.report(test=step)

        wrapped = wrap_function(train)
        new_trainable = wrapped(logger_creator=self.logger_creator)
        for i in range(2):
            result = new_trainable.train()
        checkpoint = new_trainable.save_to_object()
        new_trainable.stop()

        new_trainable2 = wrapped(logger_creator=self.logger_creator)
        new_trainable2.restore_from_object(checkpoint)
        new_trainable2.stop()

        new_trainable2 = wrapped(logger_creator=self.logger_creator)
        new_trainable2.restore_from_object(checkpoint)
        result = new_trainable2.train()
        new_trainable2.stop()
        self.assertTrue(result[TRAINING_ITERATION] == 3)

    def testReuseNullCheckpoint(self):
        def train(config, checkpoint_dir=None):
            assert not checkpoint_dir
            for step in range(10):
                tune.report(test=step)

        # Create checkpoint
        wrapped = wrap_function(train)
        checkpoint = None
        new_trainable = wrapped(logger_creator=self.logger_creator)
        new_trainable.train()
        checkpoint = new_trainable.save()
        new_trainable.stop()

        # Use the checkpoint a couple of times
        for i in range(3):
            new_trainable = wrapped(logger_creator=self.logger_creator)
            new_trainable.restore(checkpoint)
            new_trainable.stop()

        # Make sure the result is still good
        new_trainable = wrapped(logger_creator=self.logger_creator)
        new_trainable.restore(checkpoint)
        result = new_trainable.train()
        checkpoint = new_trainable.save()
        new_trainable.stop()
        self.assertTrue(result[TRAINING_ITERATION] == 1)

    def testMultipleNullCheckpoints(self):
        def train(config, checkpoint_dir=None):
            assert not checkpoint_dir
            for step in range(10):
                tune.report(test=step)

        wrapped = wrap_function(train)
        checkpoint = None
        for i in range(5):
            new_trainable = wrapped(logger_creator=self.logger_creator)
            if checkpoint:
                new_trainable.restore(checkpoint)
            result = new_trainable.train()
            checkpoint = new_trainable.save()
            new_trainable.stop()
        self.assertTrue(result[TRAINING_ITERATION] == 1)

    def testMultipleNullMemoryCheckpoints(self):
        def train(config, checkpoint_dir=None):
            assert not checkpoint_dir
            for step in range(10):
                tune.report(test=step)

        wrapped = wrap_function(train)
        checkpoint = None
        for i in range(5):
            new_trainable = wrapped(logger_creator=self.logger_creator)
            if checkpoint:
                new_trainable.restore_from_object(checkpoint)
            result = new_trainable.train()
            checkpoint = new_trainable.save_to_object()
            new_trainable.stop()
        assert result[TRAINING_ITERATION] == 1

    def testFunctionNoCheckpointing(self):
        def train(config, checkpoint_dir=None):
            if checkpoint_dir:
                assert os.path.exists(checkpoint_dir)
            for step in range(10):
                tune.report(test=step)

        wrapped = wrap_function(train)

        new_trainable = wrapped(logger_creator=self.logger_creator)
        result = new_trainable.train()
        checkpoint = new_trainable.save()
        new_trainable.stop()

        new_trainable2 = wrapped(logger_creator=self.logger_creator)
        new_trainable2.restore(checkpoint)
        result = new_trainable2.train()
        self.assertEqual(result[TRAINING_ITERATION], 1)
        checkpoint = new_trainable2.save()
        new_trainable2.stop()

    def testFunctionRecurringSave(self):
        """This tests that save and restore are commutative."""

        def train(config, checkpoint_dir=None):
            if checkpoint_dir:
                assert os.path.exists(checkpoint_dir)
            for step in range(10):
                if step % 3 == 0:
                    with tune.checkpoint_dir(step=step) as checkpoint_dir:
                        path = os.path.join(checkpoint_dir, "checkpoint")
                        with open(path, "w") as f:
                            f.write(json.dumps({"step": step}))
                tune.report(test=step)

        wrapped = wrap_function(train)

        new_trainable = wrapped(logger_creator=self.logger_creator)
        new_trainable.train()
        checkpoint_obj = new_trainable.save_to_object()
        new_trainable.restore_from_object(checkpoint_obj)
        checkpoint = new_trainable.save()

        new_trainable.stop()

        new_trainable2 = wrapped(logger_creator=self.logger_creator)
        new_trainable2.restore(checkpoint)
        new_trainable2.train()
        new_trainable2.stop()

    def testFunctionImmediateSave(self):
        """This tests that save and restore are commutative."""

        def train(config, checkpoint_dir=None):
            if checkpoint_dir:
                assert os.path.exists(checkpoint_dir)
            for step in range(10):
                with tune.checkpoint_dir(step=step) as checkpoint_dir:
                    print(checkpoint_dir)
                    path = os.path.join(checkpoint_dir, "checkpoint-{}".format(step))
                    open(path, "w").close()
                tune.report(test=step)

        wrapped = wrap_function(train)
        new_trainable = wrapped(logger_creator=self.logger_creator)
        new_trainable.train()
        new_trainable.train()
        checkpoint_obj = new_trainable.save_to_object()
        new_trainable.stop()

        new_trainable2 = wrapped(logger_creator=self.logger_creator)
        new_trainable2.restore_from_object(checkpoint_obj)
        assert sum("tmp" in path for path in os.listdir(self.logdir)) == 1
        checkpoint_obj = new_trainable2.save_to_object()
        new_trainable2.train()
        result = new_trainable2.train()
        assert sum("tmp" in path for path in os.listdir(self.logdir)) == 1
        new_trainable2.stop()
        assert sum("tmp" in path for path in os.listdir(self.logdir)) == 0
        assert result[TRAINING_ITERATION] == 4


class FunctionApiTest(unittest.TestCase):
    def setUp(self):
        ray.init(num_cpus=4, num_gpus=0, object_store_memory=150 * 1024 * 1024)

    def tearDown(self):
        ray.shutdown()
        _register_all()  # re-register the evicted objects

    def testCheckpointError(self):
        def train(config, checkpoint_dir=False):
            pass

        with self.assertRaises(ValueError):
            tune.run(train, checkpoint_config=CheckpointConfig(checkpoint_frequency=1))
        with self.assertRaises(ValueError):
            tune.run(train, checkpoint_config=CheckpointConfig(checkpoint_at_end=True))

    def testCheckpointFunctionAtEnd(self):
        def train(config, checkpoint_dir=False):
            for i in range(10):
                tune.report(test=i)
            with tune.checkpoint_dir(step=10) as checkpoint_dir:
                checkpoint_path = os.path.join(checkpoint_dir, "ckpt.log")
                with open(checkpoint_path, "w") as f:
                    f.write("hello")

        [trial] = tune.run(train).trials
        assert os.path.exists(os.path.join(trial.checkpoint.dir_or_data, "ckpt.log"))

    def testCheckpointFunctionAtEndContext(self):
        def train(config, checkpoint_dir=False):
            for i in range(10):
                tune.report(test=i)
            with tune.checkpoint_dir(step=10) as checkpoint_dir:
                checkpoint_path = os.path.join(checkpoint_dir, "ckpt.log")
                with open(checkpoint_path, "w") as f:
                    f.write("hello")

        [trial] = tune.run(train).trials
        assert os.path.exists(os.path.join(trial.checkpoint.dir_or_data, "ckpt.log"))

    def testVariousCheckpointFunctionAtEnd(self):
        def train(config, checkpoint_dir=False):
            for i in range(10):
                with tune.checkpoint_dir(step=i) as checkpoint_dir:
                    checkpoint_path = os.path.join(checkpoint_dir, "ckpt.log")
                    with open(checkpoint_path, "w") as f:
                        f.write("hello")
                tune.report(test=i)
            with tune.checkpoint_dir(step=i) as checkpoint_dir:
                checkpoint_path = os.path.join(checkpoint_dir, "ckpt.log2")
                with open(checkpoint_path, "w") as f:
                    f.write("goodbye")

        checkpoint_config = CheckpointConfig(num_to_keep=3)
        [trial] = tune.run(train, checkpoint_config=checkpoint_config).trials
        assert os.path.exists(os.path.join(trial.checkpoint.dir_or_data, "ckpt.log2"))

    def testReuseCheckpoint(self):
        def train(config, checkpoint_dir=None):
            itr = 0
            if checkpoint_dir:
                with open(os.path.join(checkpoint_dir, "ckpt.log"), "r") as f:
                    itr = int(f.read()) + 1

            for i in range(itr, config["max_iter"]):
                with tune.checkpoint_dir(step=i) as checkpoint_dir:
                    checkpoint_path = os.path.join(checkpoint_dir, "ckpt.log")
                    with open(checkpoint_path, "w") as f:
                        f.write(str(i))
                tune.report(test=i, training_iteration=i)

        [trial] = tune.run(
            train,
            config={"max_iter": 5},
        ).trials
        last_ckpt = trial.checkpoint.dir_or_data
        assert os.path.exists(os.path.join(trial.checkpoint.dir_or_data, "ckpt.log"))
        analysis = tune.run(train, config={"max_iter": 10}, restore=last_ckpt)
        trial_dfs = list(analysis.trial_dataframes.values())
        assert len(trial_dfs[0]["training_iteration"]) == 5

    def testRetry(self):
        def train(config, checkpoint_dir=None):
            restored = bool(checkpoint_dir)
            itr = 0
            if checkpoint_dir:
                with open(os.path.join(checkpoint_dir, "ckpt.log"), "r") as f:
                    itr = int(f.read()) + 1

            for i in range(itr, 10):
                if i == 5 and not restored:
                    raise Exception("try to fail me")
                with tune.checkpoint_dir(step=i) as checkpoint_dir:
                    checkpoint_path = os.path.join(checkpoint_dir, "ckpt.log")
                    with open(checkpoint_path, "w") as f:
                        f.write(str(i))
                tune.report(test=i, training_iteration=i)

        analysis = tune.run(train, max_failures=3)
        last_ckpt = analysis.trials[0].checkpoint.dir_or_data
        assert os.path.exists(os.path.join(last_ckpt, "ckpt.log"))
        trial_dfs = list(analysis.trial_dataframes.values())
        assert len(trial_dfs[0]["training_iteration"]) == 10

    def testEnabled(self):
        def train(config, checkpoint_dir=None):
            is_active = tune.is_session_enabled()
            result = {"active": is_active}
            if is_active:
                tune.report(**result)
            return result

        assert train({})["active"] is False
        analysis = tune.run(train)
        t = analysis.trials[0]
        assert t.last_result["active"], t.last_result

    def testBlankCheckpoint(self):
        def train(config, checkpoint_dir=None):
            restored = bool(checkpoint_dir)
            itr = 0
            if checkpoint_dir:
                with open(os.path.join(checkpoint_dir, "ckpt.log"), "r") as f:
                    itr = int(f.read()) + 1

            for i in range(itr, 10):
                if i == 5 and not restored:
                    raise Exception("try to fail me")
                with tune.checkpoint_dir(step=itr) as checkpoint_dir:
                    checkpoint_path = os.path.join(checkpoint_dir, "ckpt.log")
                    with open(checkpoint_path, "w") as f:
                        f.write(str(i))
                tune.report(test=i, training_iteration=i)

        analysis = tune.run(train, max_failures=3)
        trial_dfs = list(analysis.trial_dataframes.values())
        assert len(trial_dfs[0]["training_iteration"]) == 10

    def testWithParameters(self):
        class Data:
            def __init__(self):
                self.data = [0] * 500_000

        data = Data()
        data.data[100] = 1

        def train(config, data=None):
            data.data[101] = 2  # Changes are local
            tune.report(metric=len(data.data), hundred=data.data[100])

        trial_1, trial_2 = tune.run(
            with_parameters(train, data=data), num_samples=2
        ).trials

        self.assertEqual(data.data[101], 0)
        self.assertEqual(trial_1.last_result["metric"], 500_000)
        self.assertEqual(trial_1.last_result["hundred"], 1)
        self.assertEqual(trial_2.last_result["metric"], 500_000)
        self.assertEqual(trial_2.last_result["hundred"], 1)
        self.assertTrue(str(trial_1).startswith("train_"))

        # With checkpoint dir parameter
        def train(config, checkpoint_dir="DIR", data=None):
            data.data[101] = 2  # Changes are local
            tune.report(metric=len(data.data), cp=checkpoint_dir)

        trial_1, trial_2 = tune.run(
            with_parameters(train, data=data), num_samples=2
        ).trials

        self.assertEqual(data.data[101], 0)
        self.assertEqual(trial_1.last_result["metric"], 500_000)
        self.assertEqual(trial_1.last_result["cp"], "DIR")
        self.assertEqual(trial_2.last_result["metric"], 500_000)
        self.assertEqual(trial_2.last_result["cp"], "DIR")
        self.assertTrue(str(trial_1).startswith("train_"))

    def testWithParameters2(self):
        class Data:
            def __init__(self):
                import numpy as np

                self.data = np.random.rand((2 * 1024 * 1024))

        def train(config, data=None):
            tune.report(metric=len(data.data))

        trainable = tune.with_parameters(train, data=Data())
        # ray.cloudpickle will crash for some reason
        import cloudpickle as cp

        dumped = cp.dumps(trainable)
        assert sys.getsizeof(dumped) < 100 * 1024

    def testNewResources(self):
        sched = ResourceChangingScheduler(
            resources_allocation_function=(
                lambda a, b, c, d: PlacementGroupFactory([{"CPU": 2}])
            )
        )

        def train(config, checkpoint_dir=None):
            tune.report(metric=1, resources=tune.get_trial_resources())

        analysis = tune.run(
            train,
            scheduler=sched,
            stop={"training_iteration": 2},
            resources_per_trial=PlacementGroupFactory([{"CPU": 1}]),
            num_samples=1,
        )

        results_list = list(analysis.results.values())
        assert results_list[0]["resources"].head_cpus == 2.0

    def testWithParametersTwoRuns1(self):
        # Makes sure two runs in the same script but different ray sessions
        # pass (https://github.com/ray-project/ray/issues/16609)
        def train_fn(config, extra=4):
            tune.report(metric=extra)

        trainable = tune.with_parameters(train_fn, extra=8)
        out = tune.run(trainable, metric="metric", mode="max")
        self.assertEqual(out.best_result["metric"], 8)

        self.tearDown()
        self.setUp()

        def train_fn_2(config, extra=5):
            tune.report(metric=extra)

        trainable = tune.with_parameters(train_fn_2, extra=9)
        out = tune.run(trainable, metric="metric", mode="max")
        self.assertEqual(out.best_result["metric"], 9)

    def testWithParametersTwoRuns2(self):
        # Makes sure two runs in the same script
        # pass (https://github.com/ray-project/ray/issues/16609)
        def train_fn(config, extra=4):
            tune.report(metric=extra)

        def train_fn_2(config, extra=5):
            tune.report(metric=extra)

        trainable1 = tune.with_parameters(train_fn, extra=8)
        trainable2 = tune.with_parameters(train_fn_2, extra=9)

        out1 = tune.run(trainable1, metric="metric", mode="max")
        out2 = tune.run(trainable2, metric="metric", mode="max")
        self.assertEqual(out1.best_result["metric"], 8)
        self.assertEqual(out2.best_result["metric"], 9)

    def testReturnAnonymous(self):
        def train(config):
            return config["a"]

        trial_1, trial_2 = tune.run(
            train, config={"a": tune.grid_search([4, 8])}
        ).trials

        self.assertEqual(trial_1.last_result[DEFAULT_METRIC], 4)
        self.assertEqual(trial_2.last_result[DEFAULT_METRIC], 8)

    def testReturnSpecific(self):
        def train(config):
            return {"m": config["a"]}

        trial_1, trial_2 = tune.run(
            train, config={"a": tune.grid_search([4, 8])}
        ).trials

        self.assertEqual(trial_1.last_result["m"], 4)
        self.assertEqual(trial_2.last_result["m"], 8)

    def testYieldAnonymous(self):
        def train(config):
            for i in range(10):
                yield config["a"] + i

        trial_1, trial_2 = tune.run(
            train, config={"a": tune.grid_search([4, 8])}
        ).trials

        self.assertEqual(trial_1.last_result[DEFAULT_METRIC], 4 + 9)
        self.assertEqual(trial_2.last_result[DEFAULT_METRIC], 8 + 9)

    def testYieldSpecific(self):
        def train(config):
            for i in range(10):
                yield {"m": config["a"] + i}

        trial_1, trial_2 = tune.run(
            train, config={"a": tune.grid_search([4, 8])}
        ).trials

        self.assertEqual(trial_1.last_result["m"], 4 + 9)
        self.assertEqual(trial_2.last_result["m"], 8 + 9)


def test_restore_from_object_delete(tmp_path):
    """Test that temporary checkpoint directories are deleted after restoring.

    `FunctionTrainable.restore_from_object` creates a temporary checkpoint directory.
    This directory is kept around as we don't control how the user interacts with
    the checkpoint - they might load it several times, or no time at all.

    Once a new checkpoint is tracked in the status reporter, there is no need to keep
    the temporary object around anymore. This test asserts that the temporary
    checkpoint directories are then deleted.
    """
    # Create 2 checkpoints
    cp_1 = TrainableUtil.make_checkpoint_dir(str(tmp_path), index=1, override=True)
    cp_2 = TrainableUtil.make_checkpoint_dir(str(tmp_path), index=2, override=True)

    # Instantiate function trainable
    trainable = FunctionTrainable()
    trainable._logdir = str(tmp_path)
    trainable._status_reporter.set_checkpoint(cp_1)

    # Save to object and restore. This will create a temporary checkpoint directory.
    cp_obj = trainable.save_to_object()
    trainable.restore_from_object(cp_obj)

    # Assert there is at least one `checkpoint_tmpxxxxx` directory in the logdir
    assert any(path.name.startswith("checkpoint_tmp") for path in tmp_path.iterdir())

    # Track a new checkpoint. This should delete the temporary checkpoint directory.
    trainable._status_reporter.set_checkpoint(cp_2)

    # Directory should have been deleted
    assert not any(
        path.name.startswith("checkpoint_tmp") for path in tmp_path.iterdir()
    )


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main(["-v", __file__]))
