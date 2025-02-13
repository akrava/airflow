#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest
import time_machine

from airflow.models.dag import DAG, AirflowException, AirflowSkipException
from airflow.providers.amazon.aws.sensors.s3 import S3KeysUnchangedSensor

TEST_DAG_ID = "unit_tests_aws_sensor"
DEFAULT_DATE = datetime(2015, 1, 1)


class TestS3KeysUnchangedSensor:
    def setup_method(self):
        self.dag = DAG(f"{TEST_DAG_ID}test_schedule_dag_once", start_date=DEFAULT_DATE, schedule="@once")

        self.sensor = S3KeysUnchangedSensor(
            task_id="sensor_1",
            bucket_name="test-bucket",
            prefix="test-prefix/path",
            inactivity_period=12,
            poke_interval=0.1,
            min_objects=1,
            allow_delete=True,
            dag=self.dag,
        )

    def test_reschedule_mode_not_allowed(self):
        with pytest.raises(ValueError):
            S3KeysUnchangedSensor(
                task_id="sensor_2",
                bucket_name="test-bucket",
                prefix="test-prefix/path",
                poke_interval=0.1,
                mode="reschedule",
                dag=self.dag,
            )

    @pytest.mark.db_test
    def test_render_template_fields(self):
        S3KeysUnchangedSensor(
            task_id="sensor_3",
            bucket_name="test-bucket",
            prefix="test-prefix/path",
            inactivity_period=12,
            poke_interval=0.1,
            min_objects=1,
            allow_delete=True,
            dag=self.dag,
        ).render_template_fields({})

    @time_machine.travel(DEFAULT_DATE)
    def test_files_deleted_between_pokes_throw_error(self):
        self.sensor.allow_delete = False
        self.sensor.is_keys_unchanged({"a", "b"})
        with pytest.raises(AirflowException):
            self.sensor.is_keys_unchanged({"a"})

    @pytest.mark.parametrize(
        "current_objects, expected_returns, inactivity_periods",
        [
            pytest.param(
                ({"a"}, {"a", "b"}, {"a", "b", "c"}),
                (False, False, False),
                (0, 0, 0),
                id="resetting inactivity period after key change",
            ),
            pytest.param(
                ({"a", "b"}, {"a"}, {"a", "c"}),
                (False, False, False),
                (0, 0, 0),
                id="item was deleted with option `allow_delete=True`",
            ),
            pytest.param(
                ({"a"}, {"a"}, {"a"}), (False, False, True), (0, 10, 20), id="inactivity period was exceeded"
            ),
            pytest.param(
                (set(), set(), set()), (False, False, False), (0, 10, 20), id="not pass if empty key is given"
            ),
        ],
    )
    def test_key_changes(self, current_objects, expected_returns, inactivity_periods, time_machine):
        time_machine.move_to(DEFAULT_DATE)
        for current, expected, period in zip(current_objects, expected_returns, inactivity_periods):
            assert self.sensor.is_keys_unchanged(current) == expected
            assert self.sensor.inactivity_seconds == period
            time_machine.coordinates.shift(10)

    @mock.patch("airflow.providers.amazon.aws.sensors.s3.S3Hook")
    def test_poke_succeeds_on_upload_complete(self, mock_hook, time_machine):
        time_machine.move_to(DEFAULT_DATE)
        mock_hook.return_value.list_keys.return_value = {"a"}
        assert not self.sensor.poke(dict())
        time_machine.coordinates.shift(10)
        assert not self.sensor.poke(dict())
        time_machine.coordinates.shift(10)
        assert self.sensor.poke(dict())

    @pytest.mark.parametrize(
        "soft_fail, expected_exception", ((False, AirflowException), (True, AirflowSkipException))
    )
    def test_fail_is_keys_unchanged(self, soft_fail, expected_exception):
        op = S3KeysUnchangedSensor(task_id="sensor", bucket_name="test-bucket", prefix="test-prefix/path")
        op.soft_fail = soft_fail
        op.previous_objects = {"1", "2", "3"}
        current_objects = {"1", "2"}
        op.allow_delete = False
        message = "Illegal behavior: objects were deleted in"
        with pytest.raises(expected_exception, match=message):
            op.is_keys_unchanged(current_objects=current_objects)

    @pytest.mark.parametrize(
        "soft_fail, expected_exception", ((False, AirflowException), (True, AirflowSkipException))
    )
    def test_fail_execute_complete(self, soft_fail, expected_exception):
        op = S3KeysUnchangedSensor(task_id="sensor", bucket_name="test-bucket", prefix="test-prefix/path")
        op.soft_fail = soft_fail
        message = "test message"
        with pytest.raises(expected_exception, match=message):
            op.execute_complete(context={}, event={"status": "error", "message": message})
