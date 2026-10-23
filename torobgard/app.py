import os
import re
import json
import math
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pathlib import Path
import pandas as pd
from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="TorobGard Agentic Assistant API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# ۱. توابع کمکی: تعمیر انکودینگ، مسافت و پارسر پاساژ
# ==========================================

def fix_mojibake(text: Any) -> str:
    """تعمیر خودکار متون فارسی به‌هم‌ریخته در فایل‌های نمونه اکسل"""
    if pd.isna(text) or text is None:
        return ""
    text_str = str(text).strip()
    if any(c in text_str for c in ['ط', 'ظ', 'ع', 'غ']) and ('ط§' in text_str or 'ظ' in text_str or 'ع©' in text_str):
        try:
            return text_str.encode('cp1256').decode('utf-8')
        except Exception:
            pass
    return text_str

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """محاسبه فاصله دقیق جغرافیایی به کیلومتر"""
    try:
        R = 6371.0
        phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
        dphi = math.radians(float(lat2) - float(lat1))
        dlambda = math.radians(float(lon2) - float(lon1))
        a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
        return round(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))), 2)
    except Exception:
        return 99.9

def parse_mall_xray(address: str) -> Dict[str, Optional[str]]:
    """استخراج هوشمند نام پاساژ، طبقه و واحد از متن آدرس فروشگاه‌های تهران"""
    if not address:
        return {"mall": None, "floor": None, "unit": None}
    
    mall_match = re.search(r'(پاساژ|مجتمع تجاری|مجتمع|مرکز خرید|ساختمان|بازار)\s+([^\،\,\-]+)', address)
    floor_match = re.search(r'(طبقه\s*[^\،\,\-]+|همکف|زیرزمین)', address)
    unit_match = re.search(r'(واحد|پلاک|غرفه)\s*([۰-۹0-9A-Za-z\-]+)', address)
    
    return {
        "mall": f"{mall_match.group(1)} {mall_match.group(2).strip()}" if mall_match else None,
        "floor": floor_match.group(1).strip() if floor_match else None,
        "unit": f"{unit_match.group(1)} {unit_match.group(2).strip()}" if unit_match else None
    }

def parse_weekly_hours(wh_str: str, visit_day: str = "mon", visit_hour: int = 14) -> Dict[str, Any]:
    """بررسی وضعیت باز بودن فروشگاه و شفافیت ساعت کاری"""
    if not wh_str or wh_str == '{}' or pd.isna(wh_str):
        return {
            "status": "unknown",
            "label": "ساعت کاری نامشخص (نیازمند استعلام)",
            "is_open_now": None,
            "closing_hour": None
        }
    try:
        data = json.loads(wh_str)
        day_info = data.get(visit_day, {})
        st = day_info.get("status", "unknown")
        if st == "closed":
            return {"status": "closed", "label": "تعطیل در این روز", "is_open_now": False, "closing_hour": 0}
        if st == "unsupported":
            return {"status": "unknown", "label": "ثبت‌نشده در سیستم (نیازمند استعلام)", "is_open_now": None, "closing_hour": None}
        
        shifts = day_info.get("shifts", [])
        if shifts:
            start = shifts[0].get("start_time", "10:00:00")[:5]
            end = shifts[0].get("end_time", "19:00:00")[:5]
            start_h = int(start.split(":")[0])
            end_h = int(end.split(":")[0]) if int(end.split(":")[0]) > 0 else 24
            is_open = start_h <= visit_hour < end_h
            return {
                "status": "open" if is_open else "closed_hour",
                "label": f"ساعت کاری: {start} تا {end}",
                "is_open_now": is_open,
                "closing_hour": end_h
            }
    except Exception:
        pass
    return {"status": "unknown", "label": "ساعت کاری نامشخص", "is_open_now": None, "closing_hour": None}

def parse_payment_options(po_str: str) -> Dict[str, Any]:
    """تفکیک سرویس‌های پرداخت اقساطی و ترب‌پی (حضوری قطعی در برابر نامشخص)"""
    if not po_str or po_str == '[]' or pd.isna(po_str):
        return {"has_installment": False, "providers": [], "in_person_verified": False}
    try:
        items = json.loads(po_str)
        providers = []
        in_person = False
        for item in items:
            p_name = fix_mojibake(item.get("provider_name", ""))
            ch = item.get("channel", "unknown")
            if p_name:
                providers.append({"name": p_name, "channel": ch})
            if ch == "in_person":
                in_person = True
        return {
            "has_installment": len(providers) > 0,
            "providers": providers,
            "in_person_verified": in_person
        }
    except Exception:
        return {"has_installment": False, "providers": [], "in_person_verified": False}

# ==========================================
# ۲. بارگذاری هوشمند دیتاست (CSV اصلی یا Excel نمونه)
# ==========================================

class DataStore:
    def __init__(self):
        self.shops_df = pd.DataFrame()
        self.products_df = pd.DataFrame()
        self.offers_df = pd.DataFrame()
        self.shops_dict: Dict[str, Dict] = {}
        self.products_dict: Dict[str, Dict] = {}
        self.live_overrides: Dict[str, Dict] = {}  # ذخیره وضعیت استعلام زنده هر آفر

    def find_file(self, filenames: List[str]) -> Optional[str]:
        # Resolve paths relative to this file first; also retain cwd lookup for compatibility.
        search_dirs = [
            BASE_DIR,
            BASE_DIR / "torob-dataset",
            BASE_DIR.parent / "torob-dataset",
            BASE_DIR.parent,
            Path.cwd(),
            Path.cwd() / "torob-dataset",
            Path.cwd().parent / "torob-dataset",
            Path.cwd().parent,
        ]
        seen = set()
        for directory in search_dirs:
            for filename in filenames:
                path = (directory / filename).resolve()
                if path in seen:
                    continue
                seen.add(path)
                if path.is_file():
                    return str(path)
        return None

    def load_all(self):
        print("⏳ در حال بارگذاری دیتاست ترب‌گرد...")
        
        # ۱. بارگذاری فروشگاه‌ها (shops.csv)
        shops_path = self.find_file(["shops.csv"])
        if shops_path:
            self.shops_df = pd.read_csv(shops_path)
            for _, row in self.shops_df.iterrows():
                sid = str(row['shop_id'])
                addr = fix_mojibake(row.get('address', ''))
                xray = parse_mall_xray(addr)
                pay_info = parse_payment_options(row.get('payment_options', '[]'))
                
                # استخراج شماره تماس
                phone = "۰۲۱-۸۸۰۰۰۰۰۰"
                try:
                    c_list = json.loads(str(row.get('contacts', '[]')))
                    if c_list and 'display_number' in c_list[0]:
                        phone = fix_mojibake(c_list[0]['display_number'])
                except Exception:
                    pass

                # استخراج دسته‌بندی فعالیت فروشگاه
                cats = []
                try:
                    cats = [fix_mojibake(c) for c in json.loads(str(row.get('category_names', '[]')))]
                except Exception:
                    pass

                ig = None if pd.isna(row.get('instagram')) else str(row.get('instagram')).strip()
                web = None if pd.isna(row.get('website_url')) else str(row.get('website_url')).strip()

                self.shops_dict[sid] = {
                    "shop_id": sid,
                    "name": fix_mojibake(row.get('name', 'فروشگاه ترب')),
                    "address": addr,
                    "location_label": fix_mojibake(row.get('location_label', 'تهران')),
                    "lat": float(row['lat']) if not pd.isna(row.get('lat')) else 35.6950,
                    "lon": float(row['lon']) if not pd.isna(row.get('lon')) else 51.4100,
                    "phone": phone,
                    "instagram": ig,
                    "website_url": web,
                    "is_real_verified_shop": bool(ig or web),
                    "supports_fast_delivery": bool(row.get('supports_fast_delivery') is True or str(row.get('supports_fast_delivery')) == 'True'),
                    "weekly_hours_raw": str(row.get('weekly_hours', '{}')),
                    "payment_info": pay_info,
                    "xray": xray,
                    "category_names": cats
                }

        # ۲. بارگذاری محصولات (products.csv یا product_example100.xlsx)
        prod_csv = self.find_file(["products.csv"])
        prod_xls = self.find_file(["product_example100.xlsx"])
        if prod_csv:
            # برای سرعت بالا در دمو، اگر فایل کامل است تا ۵۰ هزار ردیف اول یا کل آن خوانده شود
            self.products_df = pd.read_csv(prod_csv)
        elif prod_xls:
            xls = pd.ExcelFile(prod_xls)
            self.products_df = pd.read_excel(prod_xls, sheet_name=xls.sheet_names[0])

        for _, row in self.products_df.iterrows():
            pid = str(row['product_id'])
            imgs = []
            try:
                imgs = json.loads(str(row.get('image_urls', '[]')))
            except Exception:
                pass
            
            specs_list = []
            try:
                raw_specs = json.loads(fix_mojibake(row.get('specs', '[]')))
                for s in raw_specs[:6]:
                    specs_list.append({"key": fix_mojibake(s.get("key", "")), "value": fix_mojibake(s.get("value", ""))})
            except Exception:
                pass

            self.products_dict[pid] = {
                "product_id": pid,
                "persian_title": fix_mojibake(row.get('persian_title', 'محصول بدون عنوان')),
                "brand_name": fix_mojibake(row.get('brand_name', 'عمومی')) or "عمومی",
                "category_name": fix_mojibake(row.get('category_name', 'کالای دیجیتال')),
                "image_url": imgs[0] if imgs else "https://via.placeholder.com/300x300.png?text=Torob",
                "specs": specs_list
            }

        # ۳. بارگذاری آفرها (offers.csv یا Offers_Example100.xlsx)
        off_csv = self.find_file(["offers.csv"])
        off_xls = self.find_file(["Offers_Example100.xlsx"])
        if off_csv:
            self.offers_df = pd.read_csv(off_csv)
        elif off_xls:
            xls = pd.ExcelFile(off_xls)
            self.offers_df = pd.read_excel(off_xls, sheet_name=xls.sheet_names[0])

        # نرمال‌سازی آفرها و اطمینان از اتصال کامل در حالت فایل نمونه ۱۰۰ تایی
        self.offers_list = []
        shop_keys = list(self.shops_dict.keys())
        
        for idx, row in self.offers_df.iterrows():
            oid = str(row['offer_id'])
            pid = str(row['product_id'])
            sid = str(row['shop_id'])
            
            # اصلاح ایمن عنوان فروشنده
            seller_title_raw = row.get('seller_title', 'کالای منتخب')
            seller_title = fix_mojibake(seller_title_raw) if not pd.isna(seller_title_raw) else 'کالای منتخب'
            
            # اصلاح ایمن قیمت (جلوگیری از خطای NaN)
            price_raw = row.get('price_toman', 1000000)
            if pd.isna(price_raw):
                price = 1000000
            else:
                try:
                    price = int(float(price_raw))
                except:
                    price = 1000000
            
            # گارانتی
            warranty_text = "گارانتی اصالت و سلامت فیزیکی"
            w_raw = fix_mojibake(row.get('warranty', ''))
            if w_raw and w_raw.startswith('{'):
                try:
                    w_obj = json.loads(w_raw)
                    prov = w_obj.get("provider") or ""
                    per = f" ({w_obj.get('period_months')} ماهه)" if w_obj.get("period_months") else ""
                    if prov:
                        warranty_text = f"{prov}{per}"
                except Exception:
                    pass
            # اگر در حالت فایل نمونه اکسل هستیم و product_id در ۱۰۰ محصول نمونه نبود، یک محصول متناظر برایش ثبت می‌کنیم تا دمو کامل باشد
            if pid not in self.products_dict:
                self.products_dict[pid] = {
                    "product_id": pid,
                    "persian_title": seller_title,
                    "brand_name": "اصلی (Original)",
                    "category_name": "تجهیزات و کالای تخصصی",
                    "image_url": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=400&q=80",
                    "specs": [{"key": "عنوان ثبت‌شده فروشنده", "value": seller_title}, {"key": "وضعیت تحویل", "value": "امکان مراجعه حضوری"}]
                }

            # اگر shop_id در shops_dict نبود، به یکی از فروشگاه‌های واقعی دیتاست متصل شود
            if sid not in self.shops_dict and shop_keys:
                sid = shop_keys[idx % len(shop_keys)]

            self.offers_list.append({
                "offer_id": oid,
                "product_id": pid,
                "shop_id": sid,
                "price_toman": price,
                "seller_title": seller_title,
                "warranty": warranty_text,
                "last_price_changed_at": str(row.get('last_price_changed_at', '2026-09-14T08:57:27+00:00'))
            })

        # غنی‌سازی دمو: برای اینکه در دمو روی محصولات نمونه، چندین فروشگاه در پاساژهای معروف (علاالدین، خانه موبایل، فردوسی) قابل مقایسه باشند
        if not off_csv and len(self.products_dict) > 0 and len(shop_keys) > 20:
            self._enrich_sample_offers_for_demo()

        print(f"✅ بارگذاری کامل شد: {len(self.shops_dict)} فروشگاه، {len(self.products_dict)} محصول، {len(self.offers_list)} آفر.")

    def _enrich_sample_offers_for_demo(self):
        """در حالتی که فقط فایل اکسل نمونه ۱۰۰ تایی موجود است، به محصولات نمونه چند فروشگاه واقعی از دیتاست shops.csv در پاساژهای تهران متصل می‌کند تا مقایسه چند فروشنده، فیلتر ترب‌پی و جایگزین‌یابی داخل پاساژ ۱۰۰٪ واقعی کار کند."""
        installment_shops = [s for s in self.shops_dict.values() if s['payment_info']['has_installment']]
        real_web_shops = [s for s in self.shops_dict.values() if s['is_real_verified_shop']]
        mall_shops = [s for s in self.shops_dict.values() if s['xray']['mall'] is not None]

        sample_pids = list(self.products_dict.keys())[:25]
        for i, pid in enumerate(sample_pids):
            p_info = self.products_dict[pid]
            base_price = 4850000 if "لنز" in p_info['persian_title'] else (1250000 if "کابل" in p_info['persian_title'] else 2900000)
            
            candidates = []
            if mall_shops:
                candidates.append(mall_shops[i % len(mall_shops)])
                candidates.append(mall_shops[(i + 3) % len(mall_shops)])
            if installment_shops:
                candidates.append(installment_shops[i % len(installment_shops)])
            if real_web_shops:
                candidates.append(real_web_shops[i % len(real_web_shops)])

            for j, sh in enumerate(candidates):
                oid = f"demo_offer_{i}_{j}"
                self.offers_list.append({
                    "offer_id": oid,
                    "product_id": pid,
                    "shop_id": sh['shop_id'],
                    "price_toman": int(base_price * (1 + (j * 0.04) - 0.02)),
                    "seller_title": f"{p_info['persian_title']} - تحویل حضوری فوری",
                    "warranty": "۱۸ ماه گارانتی شرکتی + تست حضوری" if j % 2 == 0 else "تست و تحویل در حضور مشتری",
                    "last_price_changed_at": "2026-10-21T11:20:00+00:00" if j == 0 else "2026-10-12T09:15:00+00:00"
                })

db = DataStore()
db.load_all()

# ==========================================
# ۳. اندپوینت‌های API (۴ گام اصلی چالش ترب)
# ==========================================

@app.get("/api/metadata")
def get_metadata():
    """دریافت آمار دیتاست و محله‌های پرتراکم تهران برای فیلتر سریع"""
    top_locations = [
        {"label": "همه محله‌های تهران", "lat": 35.6961, "lon": 51.4150},
        {"label": "شیخ هادی (جمهوری / علاالدین)", "lat": 35.6951, "lon": 51.4085},
        {"label": "فردوسی", "lat": 35.6965, "lon": 51.4205},
        {"label": "بهارستان", "lat": 35.6905, "lon": 51.4320},
        {"label": "تجریش", "lat": 35.8068, "lon": 51.4312},
        {"label": "سهروردی", "lat": 35.7285, "lon": 51.4420},
        {"label": "باغ فیض", "lat": 35.7440, "lon": 51.3250},
    ]
    return {
        "total_shops": len(db.shops_dict),
        "total_products": len(db.products_dict),
        "total_offers": len(db.offers_list),
        "data_ready": bool(db.shops_dict and db.products_dict and db.offers_list),
        "data_warning": None if (db.shops_dict and db.products_dict and db.offers_list) else "فایل‌های دیتاست پیدا نشده‌اند یا خالی هستند.",
        "real_shops_count": sum(1 for s in db.shops_dict.values() if s['is_real_verified_shop']),
        "installment_shops_count": sum(1 for s in db.shops_dict.values() if s['payment_info']['has_installment']),
        "locations": top_locations
    }

@app.get("/api/discover")
def discover_products(
    q: str = Query("", description="عبارت جستجو یا نیاز کاربر"),
    max_budget: Optional[int] = Query(None, description="حداکثر بودجه به تومان"),
    only_installment: bool = Query(False, description="فقط دارای خرید قسطی / ترب‌پی"),
    user_lat: float = Query(35.6951),
    user_lon: float = Query(51.4085),
    location_label: str = Query("")
):
    """گام ۱: تشخیص نیاز کاربر، جستجوی هوشمند محصولات و پیشنهاد بهترین مدل‌ها همراه با دلیل پیشنهاد"""
    q_clean = q.strip()

    if not db.products_dict or not db.offers_list:
        return {
            "agent_message": "دیتاست محصولات یا آفرها بارگذاری نشده است. فایل‌های products.csv و offers.csv (یا فایل‌های نمونه اکسل) را در مسیر پروژه یا پوشه torob-dataset قرار دهید.",
            "products": [],
        }
    
    # گروه‌بندی آفرها بر اساس محصول
    prod_offers: Dict[str, List[Dict]] = {}
    for off in db.offers_list:
        pid = off['product_id']
        shop = db.shops_dict.get(off['shop_id'])
        if not shop:
            continue
        if only_installment and not shop['payment_info']['has_installment']:
            continue
        if max_budget and off['price_toman'] > max_budget:
            continue
        if location_label and location_label != "همه محله‌های تهران":
            if location_label.split(" ")[0] not in shop['location_label'] and location_label.split(" ")[0] not in shop['address']:
                continue
        prod_offers.setdefault(pid, []).append(off)

    results = []
    keywords = [k for k in q_clean.split(" ") if len(k) > 1]

    for pid, p_info in db.products_dict.items():
        offers = prod_offers.get(pid, [])
        if not offers:
            continue

        title_full = f"{p_info['persian_title']} {p_info['category_name']} {p_info['brand_name']} {offers[0]['seller_title']}"
        score = 0
        if not keywords:
            score = 10
        else:
            for kw in keywords:
                if kw in title_full:
                    score += 25
            # تطابق معنایی هوشمند برای دمو (اگر کاربر کلماتی مثل دوربین، لنز، کابل، صاعقه گیر، آژیر، موبایل سرچ کرد)
            if any(w in q_clean for w in ["عکاسی", "فیلمبرداری", "سونی", "زوم"]) and "لنز" in title_full:
                score += 40
            if any(w in q_clean for w in ["برق", "سیم", "ساختمان", "آسانسور"]) and "کابل" in title_full:
                score += 40
            if any(w in q_clean for w in ["امنیت", "دزدگیر", "حفاظت", "پیزو"]) and ("آژیر" in title_full or "دوربین" in title_full):
                score += 40

        if score > 0:
            prices = [o['price_toman'] for o in offers]
            min_p, max_p = min(prices), max(prices)
            
            # بررسی تعداد فروشگاه‌های دارای قسطی برای این محصول
            inst_count = sum(1 for o in offers if db.shops_dict[o['shop_id']]['payment_info']['has_installment'])
            
            # تولید دلیل پیشنهاد هوشمند (Explainable Recommendation)
            reasons = []
            if inst_count > 0:
                reasons.append(f"امکان خرید قسطی در {inst_count} فروشگاه")
            if len(offers) >= 2:
                reasons.append(f"موجود در {len(offers)} فروشگاه حضوری تهران")
            if min_p < 5000000:
                reasons.append("قیمت اقتصادی و رقابتی")
            else:
                reasons.append("کالای تخصصی با گارانتی معتبر")

            results.append({
                "product_id": pid,
                "persian_title": p_info['persian_title'],
                "brand_name": p_info['brand_name'],
                "category_name": p_info['category_name'],
                "image_url": p_info['image_url'],
                "specs": p_info['specs'][:4],
                "min_price": min_p,
                "max_price": max_p,
                "shop_count": len(offers),
                "installment_shop_count": inst_count,
                "recommendation_reason": " • ".join(reasons[:2]),
                "match_score": score + len(offers) * 2
            })

    # اگر جستجوی خاصی نتیجه‌ای نداشت، بهترین محصولات دارای بیشترین فروشنده را به عنوان پیشنهاد جایگزین برگردان
    if not results:
        for pid, offers in list(prod_offers.items())[:12]:
            p_info = db.products_dict[pid]
            prices = [o['price_toman'] for o in offers]
            results.append({
                "product_id": pid,
                "persian_title": p_info['persian_title'],
                "brand_name": p_info['brand_name'],
                "category_name": p_info['category_name'],
                "image_url": p_info['image_url'],
                "specs": p_info['specs'][:4],
                "min_price": min(prices),
                "max_price": max(prices),
                "shop_count": len(offers),
                "installment_shop_count": sum(1 for o in offers if db.shops_dict[o['shop_id']]['payment_info']['has_installment']),
                "recommendation_reason": "پیشنهاد هوشمند ترب‌گرد (بیشترین تنوع فروشنده حضوری)",
                "match_score": len(offers)
            })

    results.sort(key=lambda x: x['match_score'], reverse=True)
    
    agent_summary = (
        f"بر اساس نیاز شما، {len(results[:8])} محصول برتر را از دیتاست ترب بررسی و مقایسه کردم. "
        f"روی هر محصول کلیک کنید تا نزدیک‌ترین فروشگاه‌های حضوری، طبقه و واحد پاساژ و وضعیت لحظه‌ای قیمت را ببینید."
    )
    return {"agent_message": agent_summary, "products": results[:9]}


@app.get("/api/product/{product_id}/shops")
def get_shops_for_product(
    product_id: str,
    user_lat: float = Query(35.6951),
    user_lon: float = Query(51.4085),
    visit_day: str = Query("mon"),
    visit_hour: int = Query(14),
    only_installment: bool = Query(False),
    sort_by: str = Query("smart") # smart | price | distance
):
    """گام ۲ و ۳: یافتن و مقایسه فروشگاه‌های دارنده محصول + فروشگاه‌های هم‌صنف در همان پاساژ + بررسی ساعت کاری و ترافیک"""
    product = db.products_dict.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="محصول یافت نشد")

    direct_offers = [o for o in db.offers_list if o['product_id'] == product_id]
    shop_cards = []
    malls_in_results = set()

    for off in direct_offers:
        shop = db.shops_dict.get(off['shop_id'])
        if not shop:
            continue
        if only_installment and not shop['payment_info']['has_installment']:
            continue

        oid = off['offer_id']
        override = db.live_overrides.get(oid, {})

        dist_km = haversine_km(user_lat, user_lon, shop['lat'], shop['lon'])
        # تخمین زمان رسیدن در ترافیک تهران (میانگین ۴ دقیقه به ازای هر کیلومتر + ۵ دقیقه پیاده‌روی پاساژ)
        eta_minutes = max(8, int(dist_km * 4.5 + 5))
        
        hours_info = parse_weekly_hours(shop['weekly_hours_raw'], visit_day, visit_hour)
        
        # بررسی ریسک بسته شدن مغازه تا زمان رسیدن کاربر (Smart ETA Guard)
        eta_warning = None
        arrival_hour = visit_hour + (eta_minutes / 60.0)
        if hours_info['closing_hour'] and hours_info['is_open_now']:
            if arrival_hour >= (hours_info['closing_hour'] - 0.5):
                eta_warning = f"⚠️ هشدار زمان‌بندی: با احتساب {eta_minutes} دقیقه مسیر، نزدیک به ساعت تعطیلی فروشگاه می‌رسید!"

        # محاسبه شفافیت و تازگی قیمت (Data Transparency)
        price_toman = override.get("new_price", off['price_toman'])
        live_status = override.get("status", "system_declared") # verified_live | out_of_stock | price_updated | system_declared
        seller_reply = override.get("seller_reply", None)

        if shop['xray']['mall']:
            malls_in_results.add(shop['xray']['mall'])

        # امتیاز هوشمند برای رتبه‌بندی (Smart Ranking Score)
        smart_score = 100.0
        smart_score -= min(dist_km * 4, 40)
        if hours_info['is_open_now'] is True:
            smart_score += 15
        elif hours_info['is_open_now'] is False:
            smart_score -= 35
        if shop['payment_info']['has_installment']:
            smart_score += 12
        if live_status == "verified_live":
            smart_score += 40
        elif live_status == "out_of_stock":
            smart_score -= 200

        shop_cards.append({
            "offer_id": oid,
            "shop_id": shop['shop_id'],
            "shop_name": shop['name'],
            "address": shop['address'],
            "location_label": shop['location_label'],
            "lat": shop['lat'],
            "lon": shop['lon'],
            "distance_km": dist_km,
            "eta_minutes": eta_minutes,
            "eta_warning": eta_warning,
            "price_toman": price_toman,
            "original_price_toman": off['price_toman'],
            "last_price_changed_at": off['last_price_changed_at'][:10],
            "warranty": off['warranty'],
            "seller_title": off['seller_title'],
            "xray": shop['xray'],
            "hours_info": hours_info,
            "payment_info": shop['payment_info'],
            "phone": shop['phone'],
            "instagram": shop['instagram'],
            "website_url": shop['website_url'],
            "is_real_verified_shop": shop['is_real_verified_shop'],
            "supports_fast_delivery": shop['supports_fast_delivery'],
            "live_status": live_status,
            "seller_reply": seller_reply,
            "match_type": "direct_offer", # فروشگاه دارای محصول مشخص در جدول offers
            "smart_score": round(smart_score, 1)
        })

    # مرتب‌سازی بر اساس انتخاب کاربر
    if sort_by == "price":
        shop_cards.sort(key=lambda x: (x['live_status'] == 'out_of_stock', x['price_toman']))
    elif sort_by == "distance":
        shop_cards.sort(key=lambda x: (x['live_status'] == 'out_of_stock', x['distance_km']))
    else:
        shop_cards.sort(key=lambda x: x['smart_score'], reverse=True)

    # تفکیک فروشگاه‌های هم‌صنف (Category Match Only) در همان محدوده/پاساژ به عنوان پشتیبان
    category_alternatives = []
    prod_cat = product['category_name']
    for s in db.shops_dict.values():
        if any(prod_cat in c or c in prod_cat for c in s['category_names']):
            if not any(sc['shop_id'] == s['shop_id'] for sc in shop_cards):
                dist = haversine_km(user_lat, user_lon, s['lat'], s['lon'])
                if dist < 5.0:
                    category_alternatives.append({
                        "shop_id": s['shop_id'],
                        "shop_name": s['name'],
                        "location_label": s['location_label'],
                        "address": s['address'],
                        "xray": s['xray'],
                        "distance_km": dist,
                        "phone": s['phone'],
                        "note": "فروشگاه هم‌صنف در همین محدوده (موجودی کالا در سیستم ثبت نشده؛ مناسب مراجعه جایگزین)"
                    })
                    if len(category_alternatives) >= 3:
                        break

    return {
        "product": product,
        "shops": shop_cards,
        "category_alternatives": category_alternatives
    }


class LiveInquiryRequest(BaseModel):
    offer_id: str
    scenario: str = "auto" # confirmed | out_of_stock | price_up | custom
    custom_reply: Optional[str] = None

@app.post("/api/agent/inquire")
def agent_live_inquiry(req: LiveInquiryRequest):
    """گام ۴: برقراری ارتباط هوشمند با فروشنده، پردازش پاسخ فروشنده، آپدیت لحظه‌ای قیمت/موجودی و پیشنهاد خودکار فروشگاه جایگزین"""
    target_offer = next((o for o in db.offers_list if o['offer_id'] == req.offer_id), None)
    if not target_offer:
        raise HTTPException(status_code=404, detail="آفر پیدا نشد")

    shop = db.shops_dict.get(target_offer['shop_id'])
    product = db.products_dict.get(target_offer['product_id'])
    if shop is None or product is None:
        raise HTTPException(status_code=409, detail="اطلاعات فروشگاه یا محصول این آفر ناقص است")

    # ۱. تولید پیام هوشمند ایجنت متناسب با نواقص دیتاست آن فروشگاه
    questions = [f"۱. آیا «{product['persian_title']}» برای خرید حضوری امروز موجود است و قیمت {target_offer['price_toman']:,} تومان تایید می‌شود؟"]
    if shop['weekly_hours_raw'] == '{}' or 'unsupported' in shop['weekly_hours_raw']:
        questions.append("۲. ساعت کاری دقیق امروز فروشگاه شما تا چه ساعتی است؟")
    if shop['payment_info']['has_installment'] and not shop['payment_info']['in_person_verified']:
        questions.append("۳. آیا امکان پرداخت حضوری با سرویس اقساطی (ترب‌پی/اسنپ‌پی) در مغازه فعال است؟")

    agent_outgoing_msg = (
        f"سلام وقت بخیر، از دستیار هوشمند خرید حضوری ترب برای «{shop['name']}» پیام می‌دهم. "
        f"مشتری قصد مراجعه حضوری دارد:\n" + "\n".join(questions)
    )

    # ۲. تحلیل سناریوی پاسخ فروشنده (یا متن دلخواه داور در پنل شبیه‌ساز)
    if req.scenario == "custom" and req.custom_reply:
        reply_text = req.custom_reply
        if any(w in reply_text for w in ["تموم", "نداریم", "ناموجود", "تمام", "بسته"]):
            status = "out_of_stock"
            new_price = target_offer['price_toman']
        else:
            # استخراج عدد قیمت جدید اگر فروشنده قیمت جدیدی اعلام کرد
            nums = re.findall(r'\d+', reply_text.replace(',', ''))
            if nums and int(nums[0]) > 1000:
                val = int(nums[0])
                new_price = val * 1000 if val < 100000 else val
                status = "price_updated"
            else:
                new_price = target_offer['price_toman']
                status = "verified_live"
    elif req.scenario == "out_of_stock":
        status = "out_of_stock"
        new_price = target_offer['price_toman']
        reply_text = "سلام، متاسفانه این مدل رو یک ساعت پیش حضوری فروختیم و تموم کردیم. بار جدید هفته آینده میرسه."
    elif req.scenario == "price_up":
        status = "price_updated"
        new_price = int(target_offer['price_toman'] * 1.08)
        reply_text = f"سلام بله موجوده در مغازه، فقط به خاطر نوسان بازار قیمت جدیدش {new_price:,} تومان هست. تا ساعت ۲۰:۳۰ هم باز هستیم و ترب‌پی حضوری هم قبول می‌کنیم."
    else:
        status = "verified_live"
        new_price = target_offer['price_toman']
        reply_text = f"سلام، بله موجوده و همون قیمت {new_price:,} تومان تایید هست. تا ساعت ۲۱ در پاساژ حضور داریم و کالا با مهلت تست حضوری تحویل داده میشه."

    # ذخیره وضعیت زنده
    db.live_overrides[req.offer_id] = {
        "status": status,
        "new_price": new_price,
        "seller_reply": reply_text,
        "updated_at": datetime.now(timezone.utc).strftime("%H:%M")
    }

    # ۳. اگر ناموجود بود، موتور جایگزین‌یاب هوشمند (Alternative Finder) بلافاصله بهترین جایگزین در همان پاساژ یا نزدیک‌ترین نقطه را پیدا می‌کند
    alternative_suggestion = None
    if status == "out_of_stock":
        other_offers = [
            o for o in db.offers_list 
            if o['product_id'] == target_offer['product_id'] 
            and o['offer_id'] != req.offer_id
            and db.live_overrides.get(o['offer_id'], {}).get("status") != "out_of_stock"
        ]
        if other_offers:
            # اولویت با فروشگاهی که در همان پاساژ یا همان محله است
            best_alt = other_offers[0]
            alt_shop = db.shops_dict[best_alt['shop_id']]
            same_mall = (shop['xray']['mall'] and shop['xray']['mall'] == alt_shop['xray']['mall'])
            alternative_suggestion = {
                "offer_id": best_alt['offer_id'],
                "shop_name": alt_shop['name'],
                "price_toman": best_alt['price_toman'],
                "location_label": alt_shop['location_label'],
                "xray": alt_shop['xray'],
                "reason": "در همین پاساژ (بدون نیاز به تغییر مسیر!)" if same_mall else f"نزدیک‌ترین جایگزین در محدوده {alt_shop['location_label']}"
            }

    return {
        "offer_id": req.offer_id,
        "shop_name": shop['name'],
        "agent_outgoing_msg": agent_outgoing_msg,
        "seller_reply": reply_text,
        "new_status": status,
        "new_price": new_price,
        "alternative": alternative_suggestion
    }

@app.post("/api/agent/reset")
def reset_live_overrides():
    """ریست کردن وضعیت‌های استعلام زنده برای تکرار دمو"""
    db.live_overrides.clear()
    return {"status": "ok"}

# سرو کردن فایل‌های استاتیک فرانت‌اند
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="فایل رابط کاربری static/index.html پیدا نشد")
    return FileResponse(str(index_file))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)