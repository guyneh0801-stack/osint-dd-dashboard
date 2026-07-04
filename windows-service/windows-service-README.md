# OSINT DD Dashboard — Windows Background Service Setup

> **Note:** All paths below assume the project is cloned to `C:\Users\guyne\Documents\osint-dd-dashboard\backend`. If your path is different, edit the `PROJECT_DIR` variable at the top of each `.ps1` script.

---

## Quick Start (3 Methods)

### Method 1: PowerShell Background (Recommended)

**Best for:** Daily use — truly hidden, full log capture, PID tracking.

```powershell
cd C:\Users\guyne\Documents\osint-dd-dashboard\backend\windows-service

# Start server (no window)
.\start-background.ps1

# Check if running
.\status.ps1

# Stop server
.\stop-server.ps1

# Restart (stop + start)
.\start-background.ps1 -Force
```

### Method 2: No-Console Batch (Double-click Friendly)

**Best for:** Quick launch — brief 1-sec flash then hidden.

Just **double-click** `start-noconsole.bat`.

### Method 3: Visible Window (Debug Mode)

**Best for:** Troubleshooting — see all logs in real-time.

Just **double-click** `start-server.bat`.

---

## Setting Paths

If your installation is in a different location, edit these lines in each `.ps1` file:

```powershell
$script:PYTHON_PATH = "C:\Users\guyne\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
$script:PROJECT_DIR = "C:\Users\guyne\Documents\osint-dd-dashboard\backend"
```

The scripts have **auto-detection** — if the configured Python path doesn't exist, they will try `py`, `python`, `python3`, and common install directories automatically.

---

## Auto-Start on Windows Boot

### Step 1: Open PowerShell as Administrator

Right-click the Start button → **Terminal (Admin)** or **PowerShell (Admin)**.

### Step 2: Run the setup script

```powershell
cd C:\Users\guyne\Documents\osint-dd-dashboard\backend\windows-service
.\setup-autostart.ps1
```

### Step 3: Remove auto-start (if needed)

```powershell
.\remove-autostart.ps1
```

---

## Troubleshooting

### "Python not found"

The script auto-detects Python. If it still fails:

1. Find your Python path:
   ```powershell
   Get-Command python
   # or
   Get-Command py
   ```
2. Edit `start-background.ps1` line 40:
   ```powershell
   $script:PYTHON_PATH = "YOUR_PYTHON_PATH_HERE"
   ```

### "Server starts but I can't access http://localhost:8000"

1. Check firewall — port 8000 might be blocked
2. Check if another app uses port 8000:
   ```powershell
   Get-NetTCPConnection -LocalPort 8000
   ```
3. Check the log file:
   ```powershell
   Get-Content ..\logs\server.log -Tail 20
   ```

### "Port 8000 is already in use"

```powershell
# Find what's using port 8000
Get-Process -Id (Get-NetTCPConnection -LocalPort 8000).OwningProcess

# Or just kill it
.\stop-server.ps1
```

### "PowerShell execution policy blocked the script"

```powershell
# Allow scripts for current user (run once)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### "The server stops when I close PowerShell"

Use `start-background.ps1` — it creates a **detached** process that survives the PowerShell window closing. The `.bat` files do the same.

---

## File Reference

| File | Purpose |
|------|---------|
| `start-background.ps1` | **Start server hidden** (primary method) |
| `stop-server.ps1` | Stop the server |
| `status.ps1` | Check server health & view logs |
| `start-server.bat` | Start with visible window (debug) |
| `start-noconsole.bat` | Start hidden via batch (1-sec flash) |
| `setup-autostart.ps1` | Register for auto-start on boot (Admin) |
| `remove-autostart.ps1` | Remove auto-start (Admin) |

---

---

# מערכת OSINT DD Dashboard — הפעלת שירות רקע ב-Windows

> **הערה:** כל הנתיבים להלן מניחים שהפרויקט שוכן ב-`C:\Users\guyne\Documents\osint-dd-dashboard\backend`. אם הנתיב אצלך שונה, ערוך את המשתנה `PROJECT_DIR` בראש כל קובץ `.ps1`.

---

## התחלה מהירה (3 שיטות)

### שיטה 1: PowerShell ברקע (מומלץ)

**הכי טוב לשימוש יומיומי** — חלון מוסתר לגמרי, לוגים מלאים, מעקב PID.

```powershell
cd C:\Users\guyne\Documents\osint-dd-dashboard\backend\windows-service

# הפעלת השרת (ללא חלון)
.\start-background.ps1

# בדיקת סטטוס
.\status.ps1

# עצירת השרת
.\stop-server.ps1

# הפעלה מחדש (עצירה + הפעלה)
.\start-background.ps1 -Force
```

### שיטה 2: קובץ Batch ללא חלון (לחיצה כפולה)

**הכי טוב להפעלה מהירה** — הבזק של שנייה ואז נעלם.

פשוט **לחץ פעמיים** על `start-noconsole.bat`.

### שיטה 3: חלון גלוי (מצב דיבאג)

**הכי טוב לאיתור באגים** — רואים את כל הלוגים בזמן אמת.

פשוט **לחץ פעמיים** על `start-server.bat`.

---

## הגדרת נתיבים

אם ההתקנה שלך נמצאת במיקום אחר, ערוך את השורות הבאות בכל קובץ `.ps1`:

```powershell
$script:PYTHON_PATH = "C:\Users\guyne\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
$script:PROJECT_DIR = "C:\Users\guyne\Documents\osint-dd-dashboard\backend"
```

הסקריפטים כוללים **גילוי אוטומטי** — אם נתיב ה-Python המוגדר לא קיים, הם ינסו את `py`, `python`, `python3`, וספריות התקנה נפוצות אוטומטית.

---

## הפעלה אוטומטית בהדלקת Windows

### שלב 1: פתח PowerShell כמנהל

לחץ קליק ימני על כפתור התפריט → **Terminal (מנהל)** או **PowerShell (מנהל)**.

### שלב 2: הפעל את סקריפט ההגדרה

```powershell
cd C:\Users\guyne\Documents\osint-dd-dashboard\backend\windows-service
.\setup-autostart.ps1
```

### שלב 3: הסרת הפעלה אוטומטית (אם צריך)

```powershell
.\remove-autostart.ps1
```

---

## פתרון תקלות

### "Python not found"

הסקריפט מגלה Python אוטומטית. אם עדיין נכשל:

1. מצא את נתיב ה-Python שלך:
   ```powershell
   Get-Command python
   # או
   Get-Command py
   ```
2. ערוך את `start-background.ps1` שורה 40:
   ```powershell
   $script:PYTHON_PATH = "YOUR_PYTHON_PATH_HERE"
   ```

### "השרת עולה אבל אין גישה ל-http://localhost:8000"

1. בדוק חומת אש — ייתכן שפורט 8000 חסום
2. בדוק אם אפליקציה אחרת משתמשת בפורט 8000:
   ```powershell
   Get-NetTCPConnection -LocalPort 8000
   ```
3. בדוק את קובץ הלוג:
   ```powershell
   Get-Content ..\logs\server.log -Tail 20
   ```

### "פורט 8000 תפוס"

```powershell
# מצא מה משתמש בפורט 8000
Get-Process -Id (Get-NetTCPConnection -LocalPort 8000).OwningProcess

# או פשוט הרוג אותו
.\stop-server.ps1
```

### "מדיניות ביצוע PowerShell חסמה את הסקריפט"

```powershell
# אפשר סקריפטים למשתמש הנוכחי (הרץ פעם אחת)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### "השרת נעצר כשסוגרים את PowerShell"

תשתמש ב-`start-background.ps1` — הוא יוצר **תהליך נפרד** ששורד את סגירת החלון. גם קבצי ה-.bat עושים את אותו דבר.

---

## רשימת קבצים

| קובץ | מטרה |
|------|---------|
| `start-background.ps1` | **הפעלת שרת מוסתרת** (שיטה ראשית) |
| `stop-server.ps1` | עצירת השרת |
| `status.ps1` | בדיקת סטטוס וצפייה בלוגים |
| `start-server.bat` | הפעלה עם חלון גלוי (דיבאג) |
| `start-noconsole.bat` | הפעלה מוסתרת דרך batch (הבזק של שנייה) |
| `setup-autostart.ps1` | רישום להפעלה אוטומטית (מנהל) |
| `remove-autostart.ps1` | הסרת הפעלה אוטומטית (מנהל) |
