"""
AISEG2温湿度センサークライアント - httpx版
既存のAiSeg2Clientと同じhttpxライブラリを使用
"""

import json
import re
import asyncio
from typing import List, Dict, Optional
import logging

import httpx

_LOGGER = logging.getLogger(__name__)


class AISEG2SensorClient:
    """AISEG2温湿度センサーデータ取得クライアント（httpx版）"""

    def __init__(self, host: str, username: str, password: str, timeout: float = 30.0):
        """
        初期化

        Args:
            host: AISEG2のIPアドレス（例: "192.168.11.216"）
            username: ユーザー名（通常 "admin"）
            password: パスワード
            timeout: タイムアウト秒数
        """
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout
        self.base_url = f"http://{host}"
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self):
        """httpxクライアントを初期化（必要な場合のみ）"""
        if self._client is None:
            def create_client():
                return httpx.AsyncClient(
                    base_url=self.base_url,
                    timeout=self.timeout,
                    auth=httpx.DigestAuth(self.username, self.password),
                    headers={"User-Agent": "aiseg2/ha-integration"},
                )
            self._client = await asyncio.to_thread(create_client)

    async def close(self):
        """HTTPクライアントをクローズ"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def async_get_sensor_data(self) -> List[Dict]:
        """
        温湿度センサーデータを取得（非同期）

        Returns:
            センサーデータの配列
        
        Raises:
            httpx.TimeoutException: タイムアウト時
            httpx.ConnectError: 接続失敗時
            httpx.HTTPStatusError: HTTPエラー時
        """
        setting_url = "/page/setting/basic/72i41?page=72i4&request_by_form=1"
        home_url = "/page/myhome/9"

        try:
            await self._ensure_client()

            # 設定ページにアクセスしてデータ取得
            _LOGGER.debug("温湿度センサーデータ取得開始: %s", self.host)
            response = await self._client.get(setting_url)
            response.raise_for_status()

            html = response.text
            devices = self._extract_sensor_data(html)

            _LOGGER.info("温湿度センサー %d個を検出: %s", len(devices), self.host)

            # 設定モードを解除（2秒待機してからホーム画面に遷移）
            await asyncio.sleep(2)
            await self._async_exit_setting_mode(home_url)

            return devices

        except httpx.TimeoutException:
            _LOGGER.error("温湿度センサーデータ取得タイムアウト: %s", self.host)
            raise
        except httpx.ConnectError:
            _LOGGER.error("温湿度センサーへの接続失敗: %s", self.host)
            raise
        except httpx.HTTPStatusError as err:
            _LOGGER.error("温湿度センサーデータ取得HTTPエラー %d: %s", 
                         err.response.status_code, self.host)
            raise
        except Exception as err:
            _LOGGER.error("温湿度センサーデータ取得エラー: %s - %s", self.host, err)
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
        match = re.search(r'init\((\{.*?\})\);</script>', html, re.DOTALL)

        if not match:
            _LOGGER.warning("温湿度センサーデータが見つかりません（init関数未検出）")
            return []

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as err:
            _LOGGER.error("温湿度センサーJSONパースエラー: %s", err)
            return []

        if 'regDevList' not in data or 'list' not in data['regDevList']:
            _LOGGER.warning("regDevList.listが見つかりません")
            return []

        devices = []
        for device in data['regDevList']['list']:
            # 温度センサー（℃を含むデバイス）のみ抽出
            if not (device.get('state') and 
                    device['state'].get('label') and 
                    '℃' in device['state']['label']):
                continue

            label = device['state']['label']

            # 温度を抽出（例: "屋外 2.6℃ 63％_S/N：1001812"）
            temp_match = re.search(r'(-?\d+(?:\.\d+)?)℃', label)
            temperature = float(temp_match.group(1)) if temp_match else None

            # 湿度を抽出（全角％に対応）
            hum_match = re.search(r'(\d+)％', label)
            humidity = int(hum_match.group(1)) if hum_match else None

            sensor_data = {
                'device_id': str(device.get('nodeId', '')),
                'name': device.get('deviceName', 'Unknown'),
                'location': device.get('location', '未設定'),
                'temperature': temperature,
                'humidity': humidity,
                'status': device['state'].get('connection', 'unknown'),
                'raw_label': label
            }

            devices.append(sensor_data)
            _LOGGER.debug("検出センサー: %s - 温度=%.1f℃, 湿度=%d%%", 
                         sensor_data['name'], 
                         temperature if temperature else 0, 
                         humidity if humidity else 0)

        return devices

    async def _async_exit_setting_mode(self, home_url: str):
        """
        設定モードを解除（非同期）
        
        設定ページから抜けるためにホーム画面にアクセス
        """
        try:
            await self._ensure_client()
            response = await self._client.get(home_url, timeout=10.0)
            response.raise_for_status()
            _LOGGER.debug("設定モードを解除しました: %s", self.host)
        except httpx.TimeoutException:
            _LOGGER.warning("設定モード解除タイムアウト（無視します）: %s", self.host)
        except Exception as err:
            _LOGGER.warning("設定モード解除に失敗（無視します）: %s - %s", self.host, err)

    async def get_sensor_by_name(self, device_name: str) -> Optional[Dict]:
        """
        特定のセンサーデータを名前で取得

        Args:
            device_name: センサー名

        Returns:
            センサーデータまたはNone
        """
        sensors = await self.async_get_sensor_data()
        for sensor in sensors:
            if sensor['name'] == device_name:
                return sensor
        return None


# テスト用のエントリーポイント
if __name__ == "__main__":
    import sys
    
    async def test():
        """テスト実行"""
        if len(sys.argv) < 4:
            print("使用方法: python sensor_client.py <host> <username> <password>")
            print("例: python sensor_client.py 192.168.11.216 admin mypassword")
            sys.exit(1)
        
        host = sys.argv[1]
        username = sys.argv[2]
        password = sys.argv[3]
        
        client = AISEG2SensorClient(host, username, password)
        
        try:
            print(f"温湿度センサーデータを取得中: {host}")
            data = await client.async_get_sensor_data()
            print(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as err:
            print(f"エラー: {err}", file=sys.stderr)
            sys.exit(1)
        finally:
            await client.close()
    
    asyncio.run(test())
