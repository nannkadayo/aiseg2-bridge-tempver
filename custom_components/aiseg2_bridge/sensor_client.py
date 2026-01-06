import json
import re
import asyncio
from typing import List, Dict, Optional
import logging

_LOGGER = logging.getLogger(__name__)


class AISEG2SensorClient:
    """AISEG2温湿度センサーデータ取得クライアント"""

    def __init__(self, host: str, username: str, password: str):
        """
        初期化

        Args:
            host: AISEG2のIPアドレス（例: "192.168.11.216"）
            username: ユーザー名（通常 "admin"）
            password: パスワード
        """
        self.host = host
        self.username = username
        self.password = password
        self.base_url = f"http://{host}"
        self._session = None

    async def async_get_sensor_data(self) -> List[Dict]:
        """
        温湿度センサーデータを取得（非同期）

        Returns:
            センサーデータの配列
        """
        import aiohttp
        from aiohttp import ClientSession, BasicAuth, DigestAuth

        setting_url = f"{self.base_url}/page/setting/basic/72i41?page=72i4&request_by_form=1"
        home_url = f"{self.base_url}/page/myhome/9"

        try:
            async with ClientSession() as session:
                # Digest認証でデータ取得
                async with session.get(
                    setting_url,
                    auth=DigestAuth(self.username, self.password),
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        raise Exception(f"HTTP Error: {response.status}")

                    html = await response.text()
                    devices = self._extract_sensor_data(html)

                # 設定モードを解除
                await asyncio.sleep(2)
                await self._async_exit_setting_mode(session, home_url)

                return devices

        except Exception as err:
            _LOGGER.error(f"センサーデータ取得エラー: {err}")
            raise

    def get_sensor_data(self) -> List[Dict]:
        """
        温湿度センサーデータを取得（同期）

        Returns:
            センサーデータの配列
        """
        import requests
        from requests.auth import HTTPDigestAuth
        import time

        setting_url = f"{self.base_url}/page/setting/basic/72i41?page=72i4&request_by_form=1"
        home_url = f"{self.base_url}/page/myhome/9"

        try:
            # Digest認証でデータ取得
            response = requests.get(
                setting_url,
                auth=HTTPDigestAuth(self.username, self.password),
                timeout=30
            )

            if response.status_code != 200:
                raise Exception(f"HTTP Error: {response.status_code}")

            devices = self._extract_sensor_data(response.text)

            # 設定モードを解除
            time.sleep(2)
            self._exit_setting_mode(home_url)

            return devices

        except Exception as err:
            _LOGGER.error(f"センサーデータ取得エラー: {err}")
            raise

    def _extract_sensor_data(self, html: str) -> List[Dict]:
        """
        HTMLから温湿度データを抽出

        Args:
            html: HTMLレスポンス

        Returns:
            センサーデータの配列
        """
        # init()関数内のJSONデータを抽出
        match = re.search(r'init\((\{.*\})\);</script>', html, re.DOTALL)

        if not match:
            _LOGGER.warning("センサーデータが見つかりません")
            return []

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as err:
            _LOGGER.error(f"JSONパースエラー: {err}")
            return []

        if 'regDevList' not in data or 'list' not in data['regDevList']:
            return []

        devices = []
        for device in data['regDevList']['list']:
            # 温度センサー（℃を含むデバイス）のみ抽出
            if not (device.get('state') and 
                    device['state'].get('label') and 
                    '℃' in device['state']['label']):
                continue

            label = device['state']['label']

            # 温度を抽出
            temp_match = re.search(r'(-?\d+(?:\.\d+)?)℃', label)
            temperature = float(temp_match.group(1)) if temp_match else None

            # 湿度を抽出（全角％に対応）
            hum_match = re.search(r'(\d+)％', label)
            humidity = int(hum_match.group(1)) if hum_match else None

            devices.append({
                'device_id': device.get('nodeId'),
                'name': device.get('deviceName'),
                'location': device.get('location', '未設定'),
                'temperature': temperature,
                'humidity': humidity,
                'status': device['state'].get('connection', 'unknown'),
                'raw_label': label
            })

        return devices

    def _exit_setting_mode(self, home_url: str):
        """設定モードを解除（同期）"""
        try:
            import requests
            from requests.auth import HTTPDigestAuth

            requests.get(
                home_url,
                auth=HTTPDigestAuth(self.username, self.password),
                timeout=10
            )
            _LOGGER.debug("設定モードを解除しました")
        except Exception as err:
            _LOGGER.warning(f"設定モード解除に失敗: {err}")

    async def _async_exit_setting_mode(self, session, home_url: str):
        """設定モードを解除（非同期）"""
        try:
            from aiohttp import DigestAuth

            async with session.get(
                home_url,
                auth=DigestAuth(self.username, self.password),
                timeout=10
            ) as response:
                _LOGGER.debug("設定モードを解除しました")
        except Exception as err:
            _LOGGER.warning(f"設定モード解除に失敗: {err}")

    def get_sensor_by_name(self, device_name: str) -> Optional[Dict]:
        """
        特定のセンサーデータを名前で取得

        Args:
            device_name: センサー名

        Returns:
            センサーデータまたはNone
        """
        sensors = self.get_sensor_data()
        for sensor in sensors:
            if sensor['name'] == device_name:
                return sensor
        return None


# 使用例
if __name__ == "__main__":
    # テスト実行
    client = AISEG2SensorClient(
        host="192.168.11.216",
        username="admin",
        password="YOUR_PASSWORD"
    )

    # 同期版
    devices = client.get_sensor_data()
    print(json.dumps(devices, indent=2, ensure_ascii=False))

    # 非同期版
    # asyncio.run(client.async_get_sensor_data())
