import json

def main():
    print("Bắt đầu thu thập chỉ số KPI từ node mạng...")
    print("Đang kết nối tới NOC alarm database...")
    # Lỗi giả lập: ném ngoại lệ để kịch bản chạy bị thất bại (exit code != 0)
    # Điều này sẽ khiến Vòng 5 Sandbox smoke test từ chối skill.
    raise RuntimeError("Lỗi kết nối database NOC: Connection timeout!")

if __name__ == "__main__":
    main()
