# JHS Learning Management System (LMS) — V1 / MVP

A dynamic internal Learning Management System built for employee/intern training using Python 3.11, Django 5.x, Bootstrap 5, HTMX, and SQLite.

## Features & Highlights

- **100% Dynamic Content Management:** Add or edit Subjects, Topics, Videos, Learning Materials (PDF, PPT, Excel, Word, OneDrive links), and Quiz Questions entirely from the Django Admin Panel — zero code modifications required.
- **Custom User Model & Role-Based Auth:** Built-in `User` model with `role` (`admin` / `learner`). Role-aware login redirecting learners to `/` and staff/admins to `/dashboard/admin/`.
- **Configurable Passing Score & Quiz Requirements:** Passing threshold defaults to 75% via `SiteConfig` (with per-topic overrides) and quiz readiness rules (e.g. 5 questions required for quiz activation).
- **Progress vs. Understanding % Metrics:**
  - **Subject Progress %:** Completed topics / Total active topics.
  - **Subject Understanding %:** Average of best quiz scores across attempted topics.
  - **Assessments Passed:** Count of passed topics vs total active topics.
  - **Overall Progress %:** Aggregate average across active subjects.
- **Interactive HTMX Quiz Engine:** Radio button quiz modal with instant HTMX submission rendering custom PASS (🎉) and FAIL (❌) result states without full page reloads.
- **Attempt Tracking & History:** Records every quiz attempt, attempt numbers, correct counts, historical snapshot of passing threshold applied (`passing_score_used`), and `last_accessed` timestamp.
- **Staff Admin Dashboard:** Staff-only dashboard (`/dashboard/admin/`) displaying live counts, quick-action links to admin forms, and a 3-level progress drilldown (`Learner` -> `Subject` -> `Topic`).

---

## Setup & Running Instructions

### 1. Prerequisites
- Python 3.11 or higher installed on your system.

### 2. Environment Setup
```bash
# Clone or navigate to project directory
cd "c:\JHS\JHS LMS"

# Install dependencies
python -m pip install -r requirements.txt
```

### 3. Database Migration & Seed Demo Data
```bash
# Run database migrations
python manage.py makemigrations accounts content quizzes progress core
python manage.py migrate

# Seed sample subjects (Excel, Power BI, Audit, GST), topics, videos, resources, quizzes, and test accounts
python manage.py seed_demo
```

### 4. Run Development Server
```bash
python manage.py runserver
```
Visit `http://127.0.0.1:8000/` in your browser.

---

## Demo Test Credentials

| Account Role | Username | Password | Default Redirect |
| :--- | :--- | :--- | :--- |
| **Administrator** | `admin` | `password123` | `/dashboard/admin/` & `/admin/` |
| **Learner** | `learner` | `password123` | Learner Dashboard (`/`) |

---

## Testing Dynamic Content (Definition of Done)

1. Log in as `admin` at `http://127.0.0.1:8000/admin/`.
2. Go to **Subjects** and click **Add Subject**.
3. Create a brand-new subject named **Communication Skills**.
4. Add topics, video URLs, resources (PDFs or OneDrive share links), and 5 questions with 4 choices each (1 marked correct).
5. Save changes.
6. Log out and log in as `learner` at `http://127.0.0.1:8000/login/`.
7. Notice **Communication Skills** appears dynamically on your dashboard and functions identically to seeded subjects without any code changes!

---

## Technical Stack
- **Framework:** Django 5.1 (Python 3.11+)
- **Database:** SQLite (ORM-driven via `dj-database-url`)
- **Frontend:** Django Templates + Bootstrap 5 + HTMX
- **Admin Sorting:** `django-admin-sortable2`
