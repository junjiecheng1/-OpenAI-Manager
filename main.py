#!/usr/bin/env python3
"""OpenAI 自動註冊工具 - 主程式（支持 Browser 模式）"""
import argparse
import json
import os
import random
import time
from datetime import datetime

from src.config import Config
from src.logger import setup_logger

# 設置日誌
logger = setup_logger(show_time=True)


def save_result(result: dict) -> None:
    """保存註冊結果"""
    email = result.get("email", "unknown").replace("@", "_")
    password = result.get("password", "")

    # 保存 token JSON
    file_name = f"token_{email}_{int(time.time())}.json"
    if Config.TOKEN_OUTPUT_DIR:
        os.makedirs(Config.TOKEN_OUTPUT_DIR, exist_ok=True)
        file_name = os.path.join(Config.TOKEN_OUTPUT_DIR, file_name)

    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info(f"Token 已保存至: {file_name}")

    # 追加帳號密碼記錄
    if result.get("email") and password:
        accounts_file = os.path.join(Config.TOKEN_OUTPUT_DIR, "accounts.txt") if Config.TOKEN_OUTPUT_DIR else "accounts.txt"
        with open(accounts_file, "a", encoding="utf-8") as af:
            af.write(f"{result['email']}----{password}\n")

    # 追加到 accounts.json（兼容 codex-auto-register 格式）
    accounts_json = os.path.join(Config.TOKEN_OUTPUT_DIR, "accounts.json") if Config.TOKEN_OUTPUT_DIR else "accounts.json"
    with open(accounts_json, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def print_banner() -> None:
    """顯示程式橫幅"""
    print("[Info] OpenAI 自動註冊工具 (Playwright Browser 模式)")
    print()
    print("=" * 60)
    print("  🌐 使用真實瀏覽器自動化，繞過所有 JS 挑戰")
    print("  📧 驗證碼通過 Cloudflare Worker 自動接收")
    print("=" * 60)
    print()


def main() -> None:
    """主函數"""
    parser = argparse.ArgumentParser(description="OpenAI 自動註冊腳本 (Playwright)")
    parser.add_argument(
        "--proxy", "-p", default=None, help="代理地址，如 http://127.0.0.1:7890"
    )
    parser.add_argument("--once", action="store_true", help="只運行一次")
    parser.add_argument("--count", "-c", type=int, default=0, help="註冊帳號數量（0=無限循環）")
    parser.add_argument("--sleep-min", "-smin", type=int, default=30, help="循環模式最短等待秒數")
    parser.add_argument("--sleep-max", "-smax", type=int, default=120, help="循環模式最長等待秒數")
    parser.add_argument("--headed", action="store_true", help="顯示瀏覽器窗口（調試用）")
    parser.add_argument("--debug", "-d", action="store_true", help="啟用調試模式")
    args = parser.parse_args()

    if args.debug:
        import logging
        logger.setLevel(logging.DEBUG)

    sleep_min = max(1, args.sleep_min)
    sleep_max = max(sleep_min, args.sleep_max)

    print_banner()

    if args.count > 0:
        logger.info(f"目標註冊數量: {args.count} 個帳號")

    # 使用 Playwright 瀏覽器注冊器
    from src.browser_registrar import BrowserRegistrar
    registrar = BrowserRegistrar(
        proxy=args.proxy,
        headless=not args.headed,
    )

    count = 0
    success_count = 0

    while True:
        count += 1
        logger.info(f">>> 開始第 {count} 次註冊流程 <<<")

        try:
            result = registrar.register_one()

            if result:
                save_result(result)
                success_count += 1
                logger.info(f"✓ 已成功註冊 {success_count} 個帳號")

                if args.count > 0 and success_count >= args.count:
                    logger.info(f"🎉 已完成目標！成功註冊 {success_count} 個帳號")
                    break
            else:
                logger.warning("本次註冊失敗")

        except Exception as e:
            logger.error(f"發生未捕獲異常: {e}", exc_info=args.debug)

        if args.once:
            break

        wait_time = random.randint(sleep_min, sleep_max)
        logger.info(f"休息 {wait_time} 秒...")
        time.sleep(wait_time)


if __name__ == "__main__":
    main()
