from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfTemperature, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed
import logging

from .const import DOMAIN
from .sensor_client import AISEG2SensorClient

_LOGGER = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

TOTAL_KEYS = [
    ("total_use_kwh", "Total Energy Today"),
    ("buy_kwh", "Purchased Energy Today"),
    ("sell_kwh", "Sold Energy Today"),
    ("gen_kwh", "Generated Energy Today"),
]

# 温湿度センサーのスキャン間隔（5分）
SENSOR_SCAN_INTERVAL = timedelta(minutes=5)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """センサーエンティティをセットアップ"""
    
    # 既存のエネルギーコーディネーター
    energy_coordinator = hass.data[DOMAIN][entry.entry_id]
    host = entry.data["host"]
    username = entry.data.get("username", "admin")
    password = entry.data["password"]
    
    ents = []
    
    # ========== 既存のエネルギーセンサー ==========
    # 合計系
    for key, name in TOTAL_KEYS:
        ents.append(TotalEnergySensor(energy_coordinator, entry, host, key, name))
    
    # 回路系
    if energy_coordinator.data:
        circuits = energy_coordinator.data.get("circuits", {})
        for cid, cdata in circuits.items():
            ents.append(CircuitEnergySensor(energy_coordinator, entry, host, cid, cdata["name"]))
    
    # ========== 新規: 温湿度センサー ==========
    try:
        # 温湿度センサークライアントを作成
        sensor_client = AISEG2SensorClient(host, username, password)
        
        # 温湿度センサー用のコーディネーターを作成
        temp_humidity_coordinator = AISEG2SensorCoordinator(hass, sensor_client)
        
        # 初回データ取得
        await temp_humidity_coordinator.async_config_entry_first_refresh()
        
        # 温湿度センサーを追加
        for device in temp_humidity_coordinator.data:
            # 温度センサー
            if device.get("temperature") is not None:
                ents.append(
                    AISEG2TemperatureSensor(
                        coordinator=temp_humidity_coordinator,
                        entry=entry,
                        host=host,
                        device_id=device["device_id"],
                        device_name=device["name"],
                        location=device["location"],
                    )
                )
            
            # 湿度センサー
            if device.get("humidity") is not None:
                ents.append(
                    AISEG2HumiditySensor(
                        coordinator=temp_humidity_coordinator,
                        entry=entry,
                        host=host,
                        device_id=device["device_id"],
                        device_name=device["name"],
                        location=device["location"],
                    )
                )
        
        _LOGGER.info(f"温湿度センサーを{len(temp_humidity_coordinator.data)}個追加しました")
        
    except Exception as err:
        _LOGGER.error(f"温湿度センサーの初期化に失敗: {err}")
        # エネルギーセンサーは動作させる
    
    async_add_entities(ents)


# ========== 温湿度センサー用コーディネーター ==========
class AISEG2SensorCoordinator(DataUpdateCoordinator):
    """AISEG2温湿度センサーデータ更新コーディネーター"""

    def __init__(self, hass: HomeAssistant, client: AISEG2SensorClient) -> None:
        """初期化"""
        super().__init__(
            hass,
            _LOGGER,
            name="AISEG2 Temperature/Humidity Sensor",
            update_interval=SENSOR_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self):
        """データを更新"""
        try:
            return await self.client.async_get_sensor_data()
        except Exception as err:
            raise UpdateFailed(f"温湿度センサーデータ更新エラー: {err}") from err


# ========== 既存のエネルギーセンサー ==========
class _Base(CoordinatorEntity, SensorEntity):
    _attr_device_class = "energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = "total"  # デイリーでリセットされる累積
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, host: str):
        super().__init__(coordinator)
        self._host = host
        self._entry = entry

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"{DOMAIN}-{self._host}")},
            "name": f"AiSEG2 ({self._host})",
            "manufacturer": "Panasonic",
            "model": "AiSEG2",
        }

    @property
    def last_reset(self) -> datetime:
        now = datetime.now(JST)
        return now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=JST)


class TotalEnergySensor(_Base):
    def __init__(self, coordinator, entry: ConfigEntry, host: str, key: str, disp_name: str):
        super().__init__(coordinator, entry, host)
        self._key = key
        self._attr_name = disp_name
        self._attr_unique_id = f"{DOMAIN}-{host}-{key}"
        # Set explicit entity_id (use underscore format of key)
        self.entity_id = f"sensor.aiseg2_bridge_{key.replace('_kwh', '')}"

    @property
    def native_value(self) -> Optional[float]:
        totals = self.coordinator.data.get("totals", {})
        v = totals.get(self._key)
        return float(v) if v is not None else None


class CircuitEnergySensor(_Base):
    def __init__(self, coordinator, entry: ConfigEntry, host: str, cid: str, cname: str):
        super().__init__(coordinator, entry, host)
        self._cid = str(cid)
        self._cname = cname
        self._attr_name = cname
        self._attr_unique_id = f"{DOMAIN}-{host}-c{self._cid}"
        # Set explicit entity_id
        self.entity_id = f"sensor.aiseg2_bridge_c{self._cid}"

    @property
    def native_value(self) -> Optional[float]:
        if not self.coordinator.data:
            return None
        circuits = self.coordinator.data.get("circuits", {})
        circuit_data = circuits.get(self._cid)
        if circuit_data:
            return float(circuit_data.get("kwh", 0))
        return None


# ========== 新規: 温湿度センサー ==========
class AISEG2BaseTempHumiditySensor(CoordinatorEntity, SensorEntity):
    """AISEG2温湿度センサーベースクラス"""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AISEG2SensorCoordinator,
        entry: ConfigEntry,
        host: str,
        device_id: str,
        device_name: str,
        location: str,
    ) -> None:
        """初期化"""
        super().__init__(coordinator)
        self._host = host
        self._entry = entry
        self._device_id = device_id
        self._device_name = device_name
        self._location = location

    @property
    def device_info(self):
        """デバイス情報"""
        return {
            "identifiers": {(DOMAIN, f"{DOMAIN}-sensor-{self._device_id}")},
            "name": f"{self._device_name} (温湿度センサー)",
            "manufacturer": "Panasonic",
            "model": "AISEG2 温湿度センサー",
            "via_device": (DOMAIN, f"{DOMAIN}-{self._host}"),
        }

    @property
    def available(self) -> bool:
        """センサーが利用可能かどうか"""
        device = self._get_device_data()
        return device is not None and device.get("status") == "online"

    def _get_device_data(self) -> Optional[dict]:
        """このデバイスのデータを取得"""
        for device in self.coordinator.data:
            if device["device_id"] == self._device_id:
                return device
        return None


class AISEG2TemperatureSensor(AISEG2BaseTempHumiditySensor):
    """AISEG2温度センサー"""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: AISEG2SensorCoordinator,
        entry: ConfigEntry,
        host: str,
        device_id: str,
        device_name: str,
        location: str,
    ) -> None:
        """初期化"""
        super().__init__(coordinator, entry, host, device_id, device_name, location)
        self._attr_unique_id = f"{DOMAIN}-{host}-{device_id}-temperature"
        self._attr_name = "Temperature"
        # 明示的なentity_id設定
        safe_name = device_name.lower().replace(" ", "_").replace("　", "_")
        self.entity_id = f"sensor.aiseg2_{safe_name}_temperature"

    @property
    def native_value(self) -> Optional[float]:
        """温度の値"""
        device = self._get_device_data()
        return device["temperature"] if device else None

    @property
    def extra_state_attributes(self):
        """追加属性"""
        device = self._get_device_data()
        if not device:
            return {}
        return {
            "location": device.get("location", "未設定"),
            "device_id": self._device_id,
        }


class AISEG2HumiditySensor(AISEG2BaseTempHumiditySensor):
    """AISEG2湿度センサー"""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        coordinator: AISEG2SensorCoordinator,
        entry: ConfigEntry,
        host: str,
        device_id: str,
        device_name: str,
        location: str,
    ) -> None:
        """初期化"""
        super().__init__(coordinator, entry, host, device_id, device_name, location)
        self._attr_unique_id = f"{DOMAIN}-{host}-{device_id}-humidity"
        self._attr_name = "Humidity"
        # 明示的なentity_id設定
        safe_name = device_name.lower().replace(" ", "_").replace("　", "_")
        self.entity_id = f"sensor.aiseg2_{safe_name}_humidity"

    @property
    def native_value(self) -> Optional[int]:
        """湿度の値"""
        device = self._get_device_data()
        return device["humidity"] if device else None

    @property
    def extra_state_attributes(self):
        """追加属性"""
        device = self._get_device_data()
        if not device:
            return {}
        return {
            "location": device.get("location", "未設定"),
            "device_id": self._device_id,
        }
