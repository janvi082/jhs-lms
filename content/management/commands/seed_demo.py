from django.core.management.base import BaseCommand
from accounts.models import User
from content.models import SiteConfig, Subject, Topic, Video, Resource
from quizzes.models import Question, Choice
from progress.services import submit_quiz_attempt, record_topic_view

class Command(BaseCommand):
    help = "Seeds initial demo subjects, topics, videos, resources, quizzes, and test accounts."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seeding JHS LMS Demo Data..."))

        # 1. SiteConfig
        config = SiteConfig.get_solo()
        config.default_passing_score = 75
        config.default_required_question_count = 5
        config.save()

        # 2. Demo Users
        admin_user, _ = User.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@jhs.com',
                'first_name': 'JHS',
                'last_name': 'Administrator',
                'role': User.ROLE_ADMIN,
                'is_staff': True,
                'is_superuser': True
            }
        )
        admin_user.set_password('password123')
        admin_user.role = User.ROLE_ADMIN
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.save()

        learner_user, _ = User.objects.get_or_create(
            username='learner',
            defaults={
                'email': 'intern001@jhs.com',
                'first_name': 'Intern',
                'last_name': '001',
                'role': User.ROLE_LEARNER
            }
        )
        learner_user.set_password('password123')
        learner_user.save()

        self.stdout.write(self.style.SUCCESS("[OK] Admin user ('admin'/'password123') and Learner ('learner'/'password123') verified."))

        # 3. Subjects & Content Data
        demo_data = [
            {
                "name": "Excel",
                "order": 1,
                "description": "Master Microsoft Excel from basic formulas to advanced lookup functions and data analysis.",
                "topics": [
                    {
                        "name": "Excel Basics & Formulas",
                        "summary": "Learn essential Excel navigation, basic formatting, cell referencing, and core mathematical formulas (SUM, AVERAGE, COUNT).",
                        "order": 1,
                        "videos": [
                            {"title": "Introduction to Excel Layout", "url": "https://onedrive.live.com/demo/excel1", "duration": "12:30", "order": 1},
                            {"title": "Basic Mathematical Formulas", "url": "https://onedrive.live.com/demo/excel2", "duration": "15:45", "order": 2},
                        ],
                        "resources": [
                            {"title": "Excel Basics Cheat Sheet", "resource_type": "pdf", "url": "https://onedrive.live.com/notes/excel-basics.pdf", "order": 1},
                            {"title": "Formula Practice Workbook", "resource_type": "excel", "url": "https://onedrive.live.com/files/practice.xlsx", "order": 2},
                        ],
                        "questions": [
                            {
                                "text": "Which formula is used to add numbers in range A1 to A10?",
                                "choices": [
                                    ("=ADD(A1:A10)", False),
                                    ("=SUM(A1:A10)", True),
                                    ("=TOTAL(A1:A10)", False),
                                    ("=PLUS(A1:A10)", False),
                                ]
                            },
                            {
                                "text": "What does a relative cell reference like A1 do when copied down a row?",
                                "choices": [
                                    ("Stays locked on A1", False),
                                    ("Adjusts automatically to A2", True),
                                    ("Throws a #REF error", False),
                                    ("Converts to text", False),
                                ]
                            },
                            {
                                "text": "Which key combination edits the currently selected cell in Excel?",
                                "choices": [
                                    ("F2", True),
                                    ("F4", False),
                                    ("Ctrl + E", False),
                                    ("Alt + Enter", False),
                                ]
                            },
                            {
                                "text": "What symbol must every Excel formula begin with?",
                                "choices": [
                                    ("+", False),
                                    ("@", False),
                                    ("=", True),
                                    ("#", False),
                                ]
                            },
                            {
                                "text": "Which function counts cells containing numbers in a range?",
                                "choices": [
                                    ("COUNTA", False),
                                    ("COUNT", True),
                                    ("COUNTBLANK", False),
                                    ("SUMIF", False),
                                ]
                            }
                        ]
                    },
                    {
                        "name": "Advanced VLOOKUP & XLOOKUP",
                        "summary": "Master vertical lookup operations, error handling with IFERROR, and modern XLOOKUP formulas for dataset matching.",
                        "order": 2,
                        "videos": [
                            {"title": "VLOOKUP Masterclass", "url": "https://onedrive.live.com/demo/vlookup", "duration": "18:20", "order": 1},
                        ],
                        "resources": [
                            {"title": "VLOOKUP vs XLOOKUP Guide", "resource_type": "pdf", "url": "https://onedrive.live.com/notes/lookups.pdf", "order": 1},
                        ],
                        "questions": [
                            {
                                "text": "In VLOOKUP, what parameter specifies an exact match lookup?",
                                "choices": [
                                    ("TRUE or 1", False),
                                    ("FALSE or 0", True),
                                    ("EXACT", False),
                                    ("MATCH", False),
                                ]
                            },
                            {
                                "text": "Which function replaces both VLOOKUP and HLOOKUP in modern Excel?",
                                "choices": [
                                    ("INDEX/MATCH", False),
                                    ("XLOOKUP", True),
                                    ("FILTER", False),
                                    ("LOOKUP2", False),
                                ]
                            },
                            {
                                "text": "If VLOOKUP fails to find a value, what default error code is returned?",
                                "choices": [
                                    ("#VALUE!", False),
                                    ("#N/A", True),
                                    ("#REF!", False),
                                    ("#NULL!", False),
                                ]
                            },
                            {
                                "text": "Can VLOOKUP search for values to the left of the lookup column?",
                                "choices": [
                                    ("Yes, always", False),
                                    ("No, it only searches columns to the right", True),
                                    ("Only if the table is sorted", False),
                                    ("Only in Excel 365", False),
                                ]
                            },
                            {
                                "text": "Which function wraps VLOOKUP to display a custom text when a lookup fails?",
                                "choices": [
                                    ("ISERROR", False),
                                    ("IFERROR", True),
                                    ("IFNA", False),
                                    ("CLEAN", False),
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "name": "Power BI",
                "order": 2,
                "description": "Learn Business Intelligence, Power Query transformations, Data Modeling, DAX calculations, and visual reports.",
                "topics": [
                    {
                        "name": "Introduction to Power BI",
                        "summary": "Overview of Power BI Desktop architecture, datasets, reports, and publishing to Power BI Service.",
                        "order": 1,
                        "videos": [
                            {"title": "Power BI Overview", "url": "https://onedrive.live.com/demo/pbi-intro", "duration": "14:10", "order": 1},
                        ],
                        "resources": [
                            {"title": "Power BI Architecture Deck", "resource_type": "ppt", "url": "https://onedrive.live.com/decks/pbi.pptx", "order": 1},
                        ],
                        "questions": [
                            {
                                "text": "What is the primary client application used to create Power BI reports?",
                                "choices": [
                                    ("Power BI Service", False),
                                    ("Power BI Desktop", True),
                                    ("Power BI Mobile", False),
                                    ("Power BI Report Server", False),
                                ]
                            },
                            {
                                "text": "Which tool inside Power BI handles data extraction and transformation?",
                                "choices": [
                                    ("DAX Editor", False),
                                    ("Power Query Editor", True),
                                    ("Data View", False),
                                    ("Model View", False),
                                ]
                            },
                            {
                                "text": "What standard file extension is used for Power BI Desktop files?",
                                "choices": [
                                    (".pbi", False),
                                    (".pbix", True),
                                    (".pbip", False),
                                    (".pbit", False),
                                ]
                            },
                            {
                                "text": "Where are published dashboards hosted for organizational sharing?",
                                "choices": [
                                    ("Local C: drive", False),
                                    ("Power BI Service (Cloud)", True),
                                    ("SQL Server", False),
                                    ("OneDrive Personal", False),
                                ]
                            },
                            {
                                "text": "Which view in Power BI Desktop defines table relationships?",
                                "choices": [
                                    ("Report View", False),
                                    ("Model View", True),
                                    ("Data View", False),
                                    ("Filter View", False),
                                ]
                            }
                        ]
                    },
                    {
                        "name": "Power Query Data Cleaning",
                        "summary": "Power Query is a data transformation tool used to import, clean, transform and combine data before using it in Power BI.",
                        "order": 2,
                        "videos": [
                            {"title": "Power Query Introduction", "url": "https://onedrive.live.com/demo/pq1", "duration": "18:42", "order": 1},
                            {"title": "Data Cleaning & Unpivoting", "url": "https://onedrive.live.com/demo/pq2", "duration": "24:15", "order": 2},
                        ],
                        "resources": [
                            {"title": "Power Query Revision Notes", "resource_type": "pdf", "url": "https://onedrive.live.com/notes/pq-revision.pdf", "order": 1},
                            {"title": "Sample Dirty Sales Data", "resource_type": "excel", "url": "https://onedrive.live.com/data/dirty-sales.xlsx", "order": 2},
                        ],
                        "questions": [
                            {
                                "text": "What is Power Query primarily used for in Power BI?",
                                "choices": [
                                    ("Creating slide presentations", False),
                                    ("Data transformation and preparation", True),
                                    ("Sending automated emails", False),
                                    ("Writing VBA macros", False),
                                ]
                            },
                            {
                                "text": "Which formula language powers Power Query transformations behind the scenes?",
                                "choices": [
                                    ("DAX", False),
                                    ("M Language", True),
                                    ("SQL", False),
                                    ("Python", False),
                                ]
                            },
                            {
                                "text": "What operation converts wide attribute columns into attribute-value pairs?",
                                "choices": [
                                    ("Pivoting", False),
                                    ("Unpivoting Columns", True),
                                    ("Group By", False),
                                    ("Merging", False),
                                ]
                            },
                            {
                                "text": "How do you combine rows from two tables with identical structures in Power Query?",
                                "choices": [
                                    ("Merge Queries", False),
                                    ("Append Queries", True),
                                    ("Join Queries", False),
                                    ("Combine Files", False),
                                ]
                            },
                            {
                                "text": "Are steps performed in Power Query recorded sequentially for automated refresh?",
                                "choices": [
                                    ("No, they must be manually re-run", False),
                                    ("Yes, listed in Applied Steps panel", True),
                                    ("Only if saved as SQL script", False),
                                    ("Only in paid licenses", False),
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "name": "Audit",
                "order": 3,
                "description": "Financial auditing principles, internal controls, risk assessment, and verification procedures.",
                "topics": [
                    {
                        "name": "Internal Audit Fundamentals",
                        "summary": "Core auditing standards, internal control evaluation, sampling methodologies, and working paper preparation.",
                        "order": 1,
                        "videos": [
                            {"title": "Audit Standards Overview", "url": "https://onedrive.live.com/demo/audit1", "duration": "20:00", "order": 1},
                        ],
                        "resources": [
                            {"title": "Audit Checklists Handbook", "resource_type": "pdf", "url": "https://onedrive.live.com/pdf/audit-checklist.pdf", "order": 1},
                        ],
                        "questions": [
                            {
                                "text": "What is the primary objective of a financial statement audit?",
                                "choices": [
                                    ("To guarantee 100% absence of error", False),
                                    ("To express an opinion on true and fair view", True),
                                    ("To prepare tax returns for clients", False),
                                    ("To write corporate press releases", False),
                                ]
                            },
                            {
                                "text": "Which risk represents the risk that material misstatement is not caught by internal controls?",
                                "choices": [
                                    ("Inherent Risk", False),
                                    ("Control Risk", True),
                                    ("Detection Risk", False),
                                    ("Audit Risk", False),
                                ]
                            },
                            {
                                "text": "What document serves as proof of audit testing performed by auditors?",
                                "choices": [
                                    ("Management representation letter", False),
                                    ("Audit Working Papers", True),
                                    ("Company prospectus", False),
                                    ("Tax invoice", False),
                                ]
                            },
                            {
                                "text": "What is substantive testing designed to detect?",
                                "choices": [
                                    ("Staff punctuality", False),
                                    ("Material misstatements in monetary values", True),
                                    ("IT network speed", False),
                                    ("Brand popularity", False),
                                ]
                            },
                            {
                                "text": "What concept requires auditors to maintain an unbiased, questioning mind?",
                                "choices": [
                                    ("Professional Cynicism", False),
                                    ("Professional Skepticism", True),
                                    ("Material Blindness", False),
                                    ("Operational Optimism", False),
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "name": "GST",
                "order": 4,
                "description": "Goods and Services Tax law, input tax credit rules, invoicing compliance, and return filing.",
                "topics": [
                    {
                        "name": "GST Framework & Taxable Event",
                        "summary": "Understanding CGST, SGST, IGST mechanisms, place of supply rules, and input tax credit (ITC) eligibility.",
                        "order": 1,
                        "videos": [
                            {"title": "GST Structure Overview", "url": "https://onedrive.live.com/demo/gst1", "duration": "16:40", "order": 1},
                        ],
                        "resources": [
                            {"title": "GST Rate Chart & Rules", "resource_type": "pdf", "url": "https://onedrive.live.com/pdf/gst-guide.pdf", "order": 1},
                        ],
                        "questions": [
                            {
                                "text": "Which tax component applies on an inter-state supply of goods?",
                                "choices": [
                                    ("CGST + SGST", False),
                                    ("IGST", True),
                                    ("UTGST only", False),
                                    ("Customs Duty", False),
                                ]
                            },
                            {
                                "text": "What is the key condition for claiming Input Tax Credit (ITC)?",
                                "choices": [
                                    ("Goods/services must be used for business purposes", True),
                                    ("Cash must be paid in advance", False),
                                    ("Customer must be a government entity", False),
                                    ("Vendor must be outside India", False),
                                ]
                            },
                            {
                                "text": "What is the taxable event under Goods and Services Tax law?",
                                "choices": [
                                    ("Manufacture of goods", False),
                                    ("Supply of goods or services", True),
                                    ("Sale of goods only", False),
                                    ("Import of capital only", False),
                                ]
                            },
                            {
                                "text": "Which monthly return summarizes outward supplies made by a registered person?",
                                "choices": [
                                    ("GSTR-3B", False),
                                    ("GSTR-1", True),
                                    ("GSTR-9", False),
                                    ("GSTR-4", False),
                                ]
                            },
                            {
                                "text": "What does PAN-based 15-digit GSTIN stand for?",
                                "choices": [
                                    ("GST Identification Number", True),
                                    ("GST Internal Network", False),
                                    ("GST Tax Identification Note", False),
                                    ("General Sales Tax Index", False),
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

        for s_data in demo_data:
            subject, _ = Subject.objects.get_or_create(
                name=s_data["name"],
                defaults={
                    "order": s_data["order"],
                    "description": s_data["description"],
                    "is_active": True
                }
            )
            for t_data in s_data["topics"]:
                topic, _ = Topic.objects.get_or_create(
                    subject=subject,
                    name=t_data["name"],
                    defaults={
                        "summary": t_data["summary"],
                        "order": t_data["order"],
                        "status": Topic.STATUS_PUBLISHED,
                        "is_active": True
                    }
                )
                
                # Videos
                for v_data in t_data.get("videos", []):
                    Video.objects.get_or_create(
                        topic=topic,
                        title=v_data["title"],
                        defaults={
                            "url": v_data["url"],
                            "duration": v_data["duration"],
                            "order": v_data["order"]
                        }
                    )
                
                # Resources
                for r_data in t_data.get("resources", []):
                    Resource.objects.get_or_create(
                        topic=topic,
                        title=r_data["title"],
                        defaults={
                            "resource_type": r_data["resource_type"],
                            "url": r_data["url"],
                            "order": r_data["order"]
                        }
                    )

                # Questions & Choices
                for q_index, q_data in enumerate(t_data.get("questions", []), start=1):
                    question, _ = Question.objects.get_or_create(
                        topic=topic,
                        text=q_data["text"],
                        defaults={"order": q_index}
                    )
                    for c_text, is_corr in q_data["choices"]:
                        Choice.objects.get_or_create(
                            question=question,
                            text=c_text,
                            defaults={"is_correct": is_corr}
                        )

        # 4. Generate demo progress for learner on Topic 1 (Excel Basics - Passed 100%)
        excel_topic_1 = Topic.objects.filter(name="Excel Basics & Formulas").first()
        if excel_topic_1:
            record_topic_view(learner_user, excel_topic_1)
            # Submit perfect score
            answers = {}
            for q in excel_topic_1.questions.all():
                corr_choice = q.choices.filter(is_correct=True).first()
                if corr_choice:
                    answers[q.id] = corr_choice.id
            submit_quiz_attempt(learner_user, excel_topic_1, answers)

        self.stdout.write(self.style.SUCCESS("[OK] Seeded demo subjects (Excel, Power BI, Audit, GST) with topics, videos, resources, quizzes, and learner activity successfully!"))
