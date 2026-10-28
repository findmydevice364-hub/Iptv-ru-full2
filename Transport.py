import requests
import subprocess
import json
import shutil

urls = [
    "http://stream1.mcquack.net/412/index.m3u8",
    "http://stream2.mcquack.net/412/index.m3u8",
]

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def analyze_m3u8_content(content):
    """Анализирует текст плейлиста m3u8"""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    
    is_master = any("EXT-X-STREAM-INF" in line for line in lines)
    
    print(  [Плейлист] Тип: {'Мастер-плейлист (содержит варианты качеств)' if is_master else 'Медиа-плейлист (содержит чанки потока)'}")
    
    if is_master:
        print("  [Варианты потока в мастер-плейлисте]:")
        for i, line in enumerate(lines):
            if "EXT-X-STREAM-INF" in line:
                # Следующая строка обычно содержит URL или относительный путь потока
                sub_url = lines[i+1] if i+1 < len(lines) else "нет URL"
                print(gpu_info := f"    - Теги: {line}")
                print(f"      Ссылка/Поток: {sub_url}")
    else:
        # Медиа-плейлист
        extinf_count = sum(1 for line in lines if line.startswith("#EXTINF"))
        print(f"  → Количество медиа-фрагментов (чанков) в манифесте: {extinf_count}")

def analyze_with_ffprobe(url):
    """Использует ffprobe для глубокого анализа потока и поиска имени канала/кодеков"""
    if not shutil.which("ffprobe"):
        print("  [!] ffprobe не найден в системе. Пропускаем глубокий анализ потока (установите ffmpeg).")
        return

    print("  [ffprobe] Запрос метаданных потока...")
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-rw_timeout", "5000000",  # таймаут 5 секунд в микросекундах
        url
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        if result.returncode != 0:
            print(f"  [X] ffprobe не смог прочитать поток (ошибка кода {result.returncode})")
            return
            
        data = json.loads(result.stdout)
        
        # Ищем информацию о формате (может содержать имя сервиса/канала)
        format_info = data.get("format", {})
        tags = format_info.get("tags", {})
        
        print("  [ffprobe] Результаты анализа:")
        if tags:
            print("    Теги потока / Канал:")
            for k, v in tags.items():
                print(f"      {k}: {v}")
        else:
            print("    Теги метаданных в формате не обнаружены.")
            
        # Ищем потоки (видео, аудио)
        streams = data.get("streams", [])
        for s in streams:
            codec_type = s.get("codec_type")
            codec_name = s.get("codec_name")
            if codec_type == "video":
                width = s.get("width")
                height = s.get("height")
                fps = s.get("r_frame_rate")
                print(f"    📹 Видео: {codec_name}, {width}x{height}, FPS: {fps}")
            elif codec_type == "audio":
                channels = s.get("channels")
                sample_rate = s.get("sample_rate")
                print(f"    🔊 Аудио: {codec_name}, {channels} кан., {sample_rate} Hz")
                
    except subprocess.TimeoutExpired:
        print("  [X] ffprobe: Превышено время ожидания ответа от потока.")
    except Exception as e:
        print(f"  [X] Ошибка при вызове ffprobe: {e}")

print("Начало комплексной проверки и анализа узлов ТВ...\n")

for url in urls:
    print(f"Проверяю узел: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=5)
        print(f"  → HTTP статус: {response.status_code}")
        
        if response.status_code == 200:
            content = response.text.strip()
            if content.startswith("#EXTM3U"):
                print("  [OK] Узел рабочий: получен валидный m3u8 плейлист.")
                
                # 1. Разбираем содержимое самого плейлиста
                analyze_m3u8_content(content)
                
                # 2. Запускаем ffprobe для определения канала, кодеков и параметров
                analyze_with_ffprobe(url)
            else:
                print("  [!] Код 200, но содержимое не является HLS-плейлистом (нет #EXTM3U).")
        else:
            print(f"  [X] Узел недоступен (статус {response.status_code}).")
            
    except requests.exceptions.Timeout:
        print("  [X] Ошибка: Таймаут запроса плейлиста.")
    except requests.exceptions.ConnectionError:
        print("  [X] Ошибка: Хост недоступен (сетевой сбой).")
    except Exception as e:
        print(f"  [X] Непредвиденная ошибка: {e}")
        
    print("-" * 60)
