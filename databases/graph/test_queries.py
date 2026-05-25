import sys
import os
import logging
# 將根目錄加入系統路徑，確保可以找到 databases 模組
sys.path.append(os.getcwd())

from databases.graph.queries import TransitQueryManager

# 設定簡易日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_queries():
    qm = None
    try:
        # 1. 初始化 Manager
        print("正在嘗試連線至 Neo4j...")
        qm = TransitQueryManager()
        
        # 2. 測試：使用已存在的 query_delay_ripple 進行測試
        # 注意：請確認 MS01 是否存在於您的資料庫中
        print("測試查詢：測試延誤漣漪效應 (使用 MS01)...")
        results = qm.query_delay_ripple(station_id="MS01", depth=1)
        
        if results is not None:
            print(f"✅ 成功查詢！共影響 {len(results)} 個站點。")
            for r in results[:3]:
                print(f" - 受影響站點: {r.get('name', 'Unknown')} (ID: {r.get('station_id')})")
        else:
            print("⚠️ 查詢成功但結果為空。")
            
        # 3. 測試：驗證連線是否保持活躍
        if qm.driver:
            qm.driver.verify_connectivity()
            print("✅ 連線驗證成功，驅動程式運作正常。")
            
    except Exception as e:
        print(f"❌ 測試失敗，錯誤訊息: {e}")
    finally:
        if qm and qm.driver:
            qm.close()
            print("連線已關閉。")

if __name__ == "__main__":
    test_queries()
    