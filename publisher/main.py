import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Tuple

import paho.mqtt.client as mqtt


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


def resolve_topic(template: str, team_no: str) -> str:
    return template.replace("{TEAM_NO}", team_no)


@dataclass
class EnvState:
    sicaklik: float = 23.5
    nem: float = 60.0
    isik: float = 400.0
    pm25: float = 18.0
    mq135_ppm_est: float = 1.5
    mq7_ppm_est: float = 2.0
    mq2_ppm_est: float = 2.5

    fan_on: bool = False
    fan_pwm: int = 0
    buzzer_on: bool = False
    decision_mode: str = "auto"
    decision_score: float = 0.0
    trend_score: float = 0.0
    gas_alarm_consecutive_hits: int = 0

    manual_until_epoch: float = 0.0
    manual_fan_on: bool = False
    manual_fan_pwm: int = 0


class PublisherService:
    def __init__(self) -> None:
        self.team_no = env("TEAM_NO", "team01")
        self.mqtt_host = env("MQTT_HOST", "localhost")
        self.mqtt_port = int(env("MQTT_PORT", "1883"))
        self.interval_s = float(env("PUBLISH_INTERVAL_SECONDS", "2"))

        telemetry_template = env("TELEMETRY_TOPIC", "{TEAM_NO}/telemetry")
        command_template = env("COMMAND_TOPIC", "{TEAM_NO}/commands")
        self.telemetry_topic = resolve_topic(telemetry_template, self.team_no)
        self.command_topic = resolve_topic(command_template, self.team_no)

        self.state = EnvState()
        self.pm25_ema = self.state.pm25
        self.mq7_ema = self.state.mq7_ppm_est
        self.mq2_ema = self.state.mq2_ppm_est
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(
        self,
        client: mqtt.Client,
        userdata,
        flags,
        reason_code,
        properties,
    ) -> None:
        if reason_code == 0:
            print(f"[publisher] MQTT baglandi: {self.mqtt_host}:{self.mqtt_port}")
            print(f"[publisher] Komut topic dinleniyor: {self.command_topic}")
            client.subscribe(self.command_topic, qos=1)
        else:
            print(f"[publisher] MQTT baglanti hatasi: {reason_code}")

    def on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print("[publisher] Gecersiz komut JSON alindi, yoksayildi.")
            return

        mode = str(payload.get("mode", "")).lower()
        if mode == "auto":
            self.state.decision_mode = "auto"
            self.state.manual_until_epoch = 0.0
            print("[publisher] Mod AUTO olarak ayarlandi.")
            return

        if mode == "manual":
            fan_on = bool(payload.get("fan_on", False))
            fan_pwm = int(payload.get("fan_pwm", 0))
            duration_s = int(payload.get("duration_s", 120))
            fan_pwm = max(0, min(255, fan_pwm))

            self.state.decision_mode = "manual"
            self.state.manual_fan_on = fan_on
            self.state.manual_fan_pwm = fan_pwm
            self.state.manual_until_epoch = time.time() + max(5, duration_s)
            print(
                "[publisher] Mod MANUAL: "
                f"fan_on={fan_on}, fan_pwm={fan_pwm}, duration_s={duration_s}"
            )

    def update_simulated_sensors(self) -> None:
        s = self.state

        # Basit bir günlük döngü yaklaşımı: isik artarsa ortam daha sıcak olma eğiliminde.
        s.isik = self._clamp(s.isik + random.uniform(-45, 55), 120, 900)
        sicaklik_target = 20.0 + (s.isik / 900.0) * 10.0
        s.sicaklik = self._clamp(
            s.sicaklik + 0.18 * (sicaklik_target - s.sicaklik) + random.uniform(-0.35, 0.35),
            16.0,
            36.0,
        )

        s.nem = self._clamp(
            s.nem + random.uniform(-1.5, 1.2) - (0.1 if s.fan_on else 0.0),
            30.0,
            90.0,
        )

        # PM2.5 ve gaz sensörlerinde zaman zaman kirli hava olayı simülasyonu.
        dirty_event = random.random() < 0.08
        pm_noise = random.uniform(-2.5, 2.8) + (8.0 if dirty_event else 0.0)
        mq135_noise = random.uniform(-0.12, 0.15) + (1.4 if dirty_event else 0.0)
        mq7_noise = random.uniform(-0.22, 0.2) + (2.2 if dirty_event else 0.0)
        mq2_noise = random.uniform(-0.25, 0.3) + (2.8 if dirty_event else 0.0)

        fan_cleaning_factor = 0.18 if s.fan_on else 0.0
        s.pm25 = self._clamp(s.pm25 + pm_noise - fan_cleaning_factor * s.pm25, 4.0, 180.0)
        s.mq135_ppm_est = self._clamp(
            s.mq135_ppm_est + mq135_noise - fan_cleaning_factor * s.mq135_ppm_est,
            0.5,
            20.0,
        )
        s.mq7_ppm_est = self._clamp(
            s.mq7_ppm_est + mq7_noise - fan_cleaning_factor * s.mq7_ppm_est,
            0.5,
            60.0,
        )
        s.mq2_ppm_est = self._clamp(
            s.mq2_ppm_est + mq2_noise - fan_cleaning_factor * s.mq2_ppm_est,
            0.5,
            70.0,
        )

    def decide_fan(self) -> Tuple[bool, int, float]:
        s = self.state
        now = time.time()
        if s.decision_mode == "manual":
            if now <= s.manual_until_epoch:
                score, trend_score = self.calculate_scores()
                self.state.trend_score = trend_score
                self.update_buzzer_state()
                return s.manual_fan_on, s.manual_fan_pwm if s.manual_fan_on else 0, score
            s.decision_mode = "auto"

        score, trend_score = self.calculate_scores()
        self.state.trend_score = trend_score

        # Histerezis: açma eşiği > kapama eşiği. Böylece sürekli aç-kapa olmaz.
        turn_on_threshold = 0.58
        turn_off_threshold = 0.42
        if s.fan_on:
            fan_on = score >= turn_off_threshold
        else:
            fan_on = score >= turn_on_threshold

        pwm = 0
        if fan_on:
            pwm = int(80 + score * 175)
            pwm = max(70, min(255, pwm))

        self.update_buzzer_state()
        return fan_on, pwm, score

    def calculate_scores(self) -> Tuple[float, float]:
        s = self.state
        pm25_n = min(s.pm25 / 120.0, 1.0)
        mq135_n = min(s.mq135_ppm_est / 12.0, 1.0)
        mq7_n = min(s.mq7_ppm_est / 30.0, 1.0)
        mq2_n = min(s.mq2_ppm_est / 30.0, 1.0)
        nem_penalty = min(abs(s.nem - 55.0) / 40.0, 1.0)

        # Katman-1: anlik kural tabanli skor
        rule_score = (0.34 * pm25_n) + (0.26 * mq7_n) + (0.2 * mq2_n) + (0.14 * mq135_n) + (0.06 * nem_penalty)
        rule_score = max(0.0, min(1.0, rule_score))

        # Katman-2: trend skoru (hafif YZ yaklasimi)
        self.pm25_ema = self._ema(self.pm25_ema, s.pm25, alpha=0.28)
        self.mq7_ema = self._ema(self.mq7_ema, s.mq7_ppm_est, alpha=0.28)
        self.mq2_ema = self._ema(self.mq2_ema, s.mq2_ppm_est, alpha=0.28)
        trend_pm25 = min(max((s.pm25 - self.pm25_ema + 30) / 60.0, 0.0), 1.0)
        trend_mq7 = min(max((s.mq7_ppm_est - self.mq7_ema + 8) / 16.0, 0.0), 1.0)
        trend_mq2 = min(max((s.mq2_ppm_est - self.mq2_ema + 8) / 16.0, 0.0), 1.0)
        trend_score = (0.45 * trend_pm25) + (0.3 * trend_mq7) + (0.25 * trend_mq2)
        trend_score = max(0.0, min(1.0, trend_score))

        final_score = (0.7 * rule_score) + (0.3 * trend_score)
        return max(0.0, min(1.0, final_score)), trend_score

    def update_buzzer_state(self) -> None:
        s = self.state
        critical = s.mq7_ppm_est >= 18.0 or s.mq2_ppm_est >= 22.0
        if critical:
            s.gas_alarm_consecutive_hits += 1
        else:
            s.gas_alarm_consecutive_hits = 0
        # Gurultu kaynakli yanlis alarmi azaltmak icin ardışık teyit.
        s.buzzer_on = s.gas_alarm_consecutive_hits >= 2

    @staticmethod
    def _ema(prev: float, current: float, alpha: float) -> float:
        return (alpha * current) + ((1.0 - alpha) * prev)

    def build_payload(self) -> Dict:
        fan_on, fan_pwm, score = self.decide_fan()
        self.state.fan_on = fan_on
        self.state.fan_pwm = fan_pwm
        self.state.decision_score = score

        return {
            "sensor_id": "vent_01",
            "values": {
                "sicaklik": round(self.state.sicaklik, 2),
                "nem": round(self.state.nem, 2),
                "isik": round(self.state.isik, 2),
                "pm25": round(self.state.pm25, 2),
                "mq135_ppm_est": round(self.state.mq135_ppm_est, 3),
                "mq7_ppm_est": round(self.state.mq7_ppm_est, 3),
                "mq2_ppm_est": round(self.state.mq2_ppm_est, 3),
                "fan_on": self.state.fan_on,
                "fan_pwm": self.state.fan_pwm,
                "buzzer_on": self.state.buzzer_on,
                "decision_score": round(self.state.decision_score, 3),
                "trend_score": round(self.state.trend_score, 3),
                "decision_mode": self.state.decision_mode,
            },
            "unit": "metric",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def run(self) -> None:
        print(f"[publisher] Telemetry topic: {self.telemetry_topic}")
        self.client.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
        self.client.loop_start()
        try:
            while True:
                self.update_simulated_sensors()
                payload = self.build_payload()
                message = json.dumps(payload)
                self.client.publish(self.telemetry_topic, message, qos=1)
                print(f"[publisher] -> {message}")
                time.sleep(self.interval_s)
        except KeyboardInterrupt:
            print("[publisher] Kapatiliyor...")
        finally:
            self.client.loop_stop()
            self.client.disconnect()

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))


if __name__ == "__main__":
    PublisherService().run()
