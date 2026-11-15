"""Налаштування керування сайтом для КЗ «НРЦ «Оберіг»»."""
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.db.models import (
    BooleanField,
    Case,
    Count,
    Exists,
    OuterRef,
    Q,
    TextField,
    Value,
    When,
)
from django.db.models.functions import Cast
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.html import format_html
from django.views.decorators.http import require_GET
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    MAX_PDF_SIZE_MB,
    PAGE_PLACEHOLDER_PHRASES,
    Block,
    ContactMessage,
    Document,
    HomePage,
    NewsItem,
    Page,
    Person,
    Section,
    SiteSettings,
    Sponsor,
)

# --- Заголовки керування сайтом українською ---
admin.site.site_header = settings.ADMIN_SITE_NAME
admin.site.site_title = settings.ADMIN_SITE_NAME
admin.site.index_title = settings.ADMIN_SITE_NAME

# --- Порядок розділів на головній сторінці керування сайтом ---
ADMIN_MODEL_ORDER = {
    "Page": 1,
    "Section": 2,
    "NewsItem": 3,
    "ContactMessage": 4,
    "Person": 5,
    "Document": 6,
    "HomePage": 7,
    "SiteSettings": 8,
    "Sponsor": 9,
}

_orig_get_app_list = admin.site.get_app_list


def _ordered_get_app_list(request, app_label=None):
    app_list = _orig_get_app_list(request, app_label)
    for app in app_list:
        if app["app_label"] == "core":
            app["models"].sort(
                key=lambda m: ADMIN_MODEL_ORDER.get(m["object_name"], 99)
            )
    return app_list


admin.site.get_app_list = _ordered_get_app_list

# --- Загальний пошук по вмісту сайту в керуванні сайтом ---
ADMIN_SEARCH_MIN_LENGTH = 2
ADMIN_SEARCH_LIMIT_PER_TYPE = 20


def _extract_block_data_strings(data) -> list[str]:
    """Рекурсивно витягує лише текстові значення з поля Block.data, ігноруючи ключі JSON-об'єктів."""
    if isinstance(data, str):
        return [data]
    if isinstance(data, list):
        texts: list[str] = []
        for item in data:
            texts.extend(_extract_block_data_strings(item))
        return texts
    if isinstance(data, dict):
        texts = []
        for val in data.values():
            texts.extend(_extract_block_data_strings(val))
        return texts
    return []


def _user_can_view_or_change(request, model) -> bool:
    """Перевіряє, чи має поточний користувач право перегляду або зміни записів моделі."""
    model_admin = admin.site._registry.get(model)
    if model_admin is None:
        return False
    return model_admin.has_view_or_change_permission(request)


@require_GET
def admin_search_view(request):
    """Сторінка загального пошуку по вмісту сайту в керуванні сайтом."""
    query = (request.GET.get("q") or "").strip()
    too_short = 0 < len(query) < ADMIN_SEARCH_MIN_LENGTH
    groups = []
    total_found = 0

    if len(query) >= ADMIN_SEARCH_MIN_LENGTH:
        q_folded = query.casefold()

        # 1. Сторінки сайту (Page) + текстові значення блоків (Block)
        if _user_can_view_or_change(request, Page):
            candidate_data_blocks = (
                Block.objects.exclude(data__isnull=True)
                .annotate(data_text=Cast("data", output_field=TextField()))
                .filter(data_text__icontains=query)
                .values_list("page_id", "data")
            )
            matching_data_page_ids = {
                page_id
                for page_id, block_data in candidate_data_blocks
                if any(
                    q_folded in val.casefold()
                    for val in _extract_block_data_strings(block_data)
                )
            }
            text_block_subquery = Block.objects.filter(
                page=OuterRef("pk"),
                text__icontains=query,
            )
            page_q = (
                Q(title__icontains=query)
                | Q(lead__icontains=query)
                | Q(slug__icontains=query)
                | Q(meta_description__icontains=query)
                | Exists(text_block_subquery)
            )
            if matching_data_page_ids:
                page_q |= Q(pk__in=matching_data_page_ids)

            pages = list(
                Page.objects.select_related("section")
                .filter(page_q)
                .order_by("section__order", "order", "id")[
                    :ADMIN_SEARCH_LIMIT_PER_TYPE
                ]
            )
            if pages:
                items = [
                    {
                        "title": p.title,
                        "section_or_type": f"Сторінка · {p.section.title}",
                        "status_text": (
                            "Опубліковано" if p.is_published else "Чернетка"
                        ),
                        "is_published": bool(p.is_published),
                        "edit_url": reverse(
                            "admin:core_page_change", args=[p.pk]
                        ),
                    }
                    for p in pages
                ]
                groups.append(
                    {
                        "label": "Сторінки сайту",
                        "count": len(items),
                        "items": items,
                    }
                )
                total_found += len(items)

        # 2. Розділи сайту (Section)
        if _user_can_view_or_change(request, Section):
            sections = list(
                Section.objects.filter(
                    Q(title__icontains=query)
                    | Q(lead__icontains=query)
                    | Q(slug__icontains=query)
                ).order_by("order", "id")[:ADMIN_SEARCH_LIMIT_PER_TYPE]
            )
            if sections:
                items = [
                    {
                        "title": s.title,
                        "section_or_type": f"Розділ меню (/{s.slug}/)",
                        "status_text": (
                            "Опубліковано (в меню)"
                            if s.show_in_menu
                            else "Приховано з меню"
                        ),
                        "is_published": bool(s.show_in_menu),
                        "edit_url": reverse(
                            "admin:core_section_change", args=[s.pk]
                        ),
                    }
                    for s in sections
                ]
                groups.append(
                    {
                        "label": "Розділи сайту",
                        "count": len(items),
                        "items": items,
                    }
                )
                total_found += len(items)

        # 3. Новини (NewsItem)
        if _user_can_view_or_change(request, NewsItem):
            news_list = list(
                NewsItem.objects.filter(
                    Q(title__icontains=query)
                    | Q(summary__icontains=query)
                    | Q(body__icontains=query)
                    | Q(category__icontains=query)
                    | Q(slug__icontains=query)
                ).order_by("-published_date", "-id")[
                    :ADMIN_SEARCH_LIMIT_PER_TYPE
                ]
            )
            if news_list:
                items = [
                    {
                        "title": n.title,
                        "section_or_type": (
                            f"Новина · {n.category} ({n.published_date:%d.%m.%Y})"
                        ),
                        "status_text": (
                            "Опубліковано" if n.is_published else "Чернетка"
                        ),
                        "is_published": bool(n.is_published),
                        "edit_url": reverse(
                            "admin:core_newsitem_change", args=[n.pk]
                        ),
                    }
                    for n in news_list
                ]
                groups.append(
                    {
                        "label": "Новини",
                        "count": len(items),
                        "items": items,
                    }
                )
                total_found += len(items)

        # 4. Документи (Document)
        if _user_can_view_or_change(request, Document):
            docs = list(
                Document.objects.select_related("page", "page__section")
                .filter(
                    Q(title__icontains=query) | Q(page__title__icontains=query)
                )
                .order_by("page__section__order", "page__order", "order", "id")[
                    :ADMIN_SEARCH_LIMIT_PER_TYPE
                ]
            )
            if docs:
                items = []
                for d in docs:
                    if not d.page.is_published:
                        doc_status = "Сторінка в чернетці"
                        doc_pub = False
                    elif d.has_file:
                        doc_status = "Опубліковано (PDF завантажено)"
                        doc_pub = True
                    else:
                        doc_status = "Без файлу (готується)"
                        doc_pub = False
                    items.append(
                        {
                            "title": d.title,
                            "section_or_type": (
                                f"Документ · {d.page.section.title} → {d.page.title}"
                            ),
                            "status_text": doc_status,
                            "is_published": doc_pub,
                            "edit_url": reverse(
                                "admin:core_document_change", args=[d.pk]
                            ),
                        }
                    )
                groups.append(
                    {
                        "label": "Документи",
                        "count": len(items),
                        "items": items,
                    }
                )
                total_found += len(items)

        # 5. Працівники (Person)
        if _user_can_view_or_change(request, Person):
            people = list(
                Person.objects.select_related("page", "page__section")
                .filter(
                    Q(full_name__icontains=query)
                    | Q(position__icontains=query)
                    | Q(page__title__icontains=query)
                )
                .order_by("page__section__order", "page__order", "order", "id")[
                    :ADMIN_SEARCH_LIMIT_PER_TYPE
                ]
            )
            if people:
                items = []
                for p in people:
                    name_label = (
                        f"{p.full_name} — {p.position}"
                        if p.full_name
                        else f"ПІБ не вказано — {p.position}"
                    )
                    if p.consent_given and p.page.is_published:
                        p_status = "Опубліковано (є згода)"
                        p_pub = True
                    elif p.consent_given:
                        p_status = "Сторінка в чернетці"
                        p_pub = False
                    else:
                        p_status = "Приховано (немає згоди)"
                        p_pub = False
                    items.append(
                        {
                            "title": name_label,
                            "section_or_type": (
                                f"Працівник · {p.page.section.title} → {p.page.title}"
                            ),
                            "status_text": p_status,
                            "is_published": p_pub,
                            "edit_url": reverse(
                                "admin:core_person_change", args=[p.pk]
                            ),
                        }
                    )
                groups.append(
                    {
                        "label": "Працівники",
                        "count": len(items),
                        "items": items,
                    }
                )
                total_found += len(items)

        # 6. Спонсори (Sponsor)
        if _user_can_view_or_change(request, Sponsor):
            sponsors = list(
                Sponsor.objects.filter(
                    Q(name__icontains=query) | Q(url__icontains=query)
                ).order_by("order", "id")[:ADMIN_SEARCH_LIMIT_PER_TYPE]
            )
            if sponsors:
                items = [
                    {
                        "title": sp.name,
                        "section_or_type": "Спонсор · Головна сторінка",
                        "status_text": (
                            "Опубліковано (з логотипом)"
                            if sp.has_logo
                            else "Опубліковано (без логотипа)"
                        ),
                        "is_published": True,
                        "edit_url": reverse(
                            "admin:core_sponsor_change", args=[sp.pk]
                        ),
                    }
                    for sp in sponsors
                ]
                groups.append(
                    {
                        "label": "Спонсори",
                        "count": len(items),
                        "items": items,
                    }
                )
                total_found += len(items)

    context = {
        **admin.site.each_context(request),
        "title": "Пошук по вмісту сайту",
        "query": query,
        "too_short": too_short,
        "min_length": ADMIN_SEARCH_MIN_LENGTH,
        "limit_per_type": ADMIN_SEARCH_LIMIT_PER_TYPE,
        "groups": groups,
        "total_found": total_found,
    }
    return render(request, "admin/search_results.html", context)


_orig_get_urls = admin.site.get_urls


def _get_urls_with_search():
    return [
        path(
            "search/",
            admin.site.admin_view(admin_search_view),
            name="search",
        ),
    ] + _orig_get_urls()


admin.site.get_urls = _get_urls_with_search


# --- Форма для блоку вмісту (без показу JSON редактору) ---
class BlockAdminForm(forms.ModelForm):
    """Зручна форма для редагування блоків сторінки без роботи з JSON."""

    items_text = forms.CharField(
        label="Пункти списку (кожен пункт з нового рядка)",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": "Перший пункт\nДругий пункт\nТретій пункт",
            }
        ),
        help_text="Використовується для маркованого списку або нумерованих кроків. Кожен рядок стане окремим пунктом.",
    )
    table_caption = forms.CharField(
        label="Заголовок (підпис) таблиці",
        required=False,
        widget=forms.TextInput(
            attrs={"placeholder": "Наприклад: Режим роботи закладу"}
        ),
        help_text="Коротка назва таблиці (обов'язкова для доступності).",
    )
    table_text = forms.CharField(
        label="Таблиця (перший рядок: заголовки через « | », далі рядки)",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": (
                    "Дні | Час\n"
                    "Понеділок — п’ятниця | 08:30 – 17:00\n"
                    "Субота, неділя | вихідні"
                ),
            }
        ),
        help_text=(
            "Перший рядок — назви колонок, розділені знаком « | ». "
            "Кожен наступний рядок — дані клітинок через « | »."
        ),
    )

    class Meta:
        model = Block
        fields = (
            "block_type",
            "order",
            "text",
            "items_text",
            "table_caption",
            "table_text",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.data:
            btype = self.instance.block_type
            data = self.instance.data
            if btype in Block.JSON_LIST_TYPES and isinstance(data, list):
                self.fields["items_text"].initial = "\n".join(
                    str(item) for item in data
                )
            elif btype == Block.BlockType.TABLE and isinstance(data, dict):
                self.fields["table_caption"].initial = data.get("caption", "")
                cols = data.get("columns", [])
                rows = data.get("rows", [])
                lines = []
                if cols:
                    lines.append(" | ".join(str(c) for c in cols))
                for row in rows:
                    lines.append(" | ".join(str(c) for c in row))
                self.fields["table_text"].initial = "\n".join(lines)

    def clean(self):
        cleaned_data = super().clean()
        block_type = cleaned_data.get("block_type")
        text = (cleaned_data.get("text") or "").strip()
        items_text = (cleaned_data.get("items_text") or "").strip()
        table_caption = (cleaned_data.get("table_caption") or "").strip()
        table_text = (cleaned_data.get("table_text") or "").strip()

        if block_type in Block.TEXT_TYPES:
            if not text:
                self.add_error(
                    "text",
                    "Введіть текст для цього блоку (абзацу, підзаголовка або примітки).",
                )
            cleaned_data["text"] = text
            self.instance.text = text
            self.instance.data = None

        elif block_type in Block.JSON_LIST_TYPES:
            items = [line.strip() for line in items_text.splitlines() if line.strip()]
            if not items:
                self.add_error(
                    "items_text",
                    "Введіть щонайменше один пункт списку (кожен пункт з нового рядка).",
                )
            cleaned_data["text"] = ""
            self.instance.text = ""
            self.instance.data = items

        elif block_type == Block.BlockType.TABLE:
            if not table_caption:
                self.add_error(
                    "table_caption",
                    "Вкажіть короткий заголовок (підпис) таблиці.",
                )
            lines = [line.strip() for line in table_text.splitlines() if line.strip()]
            if len(lines) < 2:
                self.add_error(
                    "table_text",
                    "Таблиця має містити щонайменше 2 рядки: перший рядок — заголовки колонок через « | », другий — дані.",
                )
                self.instance.text = ""
                self.instance.data = None
                return cleaned_data

            columns = [col.strip() for col in lines[0].split("|")]
            if not all(columns):
                self.add_error(
                    "table_text",
                    "У першому рядку всі колонки повинні мати назву (не залишайте порожніх місць між « | »).",
                )
                return cleaned_data

            col_count = len(columns)
            rows = []
            for row_idx, line in enumerate(lines[1:], start=2):
                cells = [cell.strip() for cell in line.split("|")]
                if len(cells) != col_count:
                    self.add_error(
                        "table_text",
                        f"У рядку №{row_idx} вказано {len(cells)} значень, а колонок у заголовку — {col_count}. "
                        f"Перевірте кількість роздільників « | ».",
                    )
                    return cleaned_data
                rows.append(cells)

            cleaned_data["text"] = ""
            self.instance.text = ""
            self.instance.data = {
                "caption": table_caption,
                "columns": columns,
                "rows": rows,
            }

        return cleaned_data

    def _post_clean(self):
        if self.errors:
            return
        super()._post_clean()


# --- Базовий клас для одиничних записів (SiteSettings, HomePage) ---
class SingletonModelAdmin(SimpleHistoryAdmin):
    """Адмін-клас для моделей з єдиним записом: одразу відкриває редагування."""

    def has_add_permission(self, request):
        return not self.model.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj, _ = self.model.objects.get_or_create(pk=1)
        url = reverse(
            f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_change",
            args=[obj.pk],
        )
        return HttpResponseRedirect(url)


# --- Вбудовані таблиці (Inlines) для сторінки Page ---
class BlockInline(admin.StackedInline):
    model = Block
    form = BlockAdminForm
    extra = 0
    classes = ["collapse"]
    verbose_name = "Блок тексту, списку або таблиці"
    verbose_name_plural = "1. Текстові блоки, списки та таблиці сторінки"
    description = (
        "Тут редагується основний текст сторінки: абзаци, підзаголовки, списки та таблиці. "
        "Поле «Порядок на сторінці» задається числом (1, 2, 3...) — менше число показується вище."
    )


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    classes = ["collapse"]
    fields = ("order", "title", "file", "show_st30_notice")
    verbose_name = "Документ (PDF)"
    verbose_name_plural = "2. Офіційні документи (PDF) на цій сторінці"
    description = (
        f"Прикріпіть PDF-файли (до {MAX_PDF_SIZE_MB} МБ), які мають відображатися на сторінці. "
        "Якщо файл ще не завантажено (поле файлу порожнє), документ показується на сайті "
        "з підписом «Документ готується до оприлюднення». Порядок задається числом."
    )


class PersonInline(admin.TabularInline):
    model = Person
    extra = 0
    classes = ["collapse"]
    fields = ("order", "position", "full_name", "photo", "consent_given")
    verbose_name = "Картка працівника"
    verbose_name_plural = "3. Працівники на цій сторінці (Адміністрація / Педагогічний колектив)"
    description = (
        "Картки працівників закладу. УВАГА: картка з'явиться на сайті ЛИШЕ тоді, "
        "коли заповнено ПІБ і поставлено галочку «Є письмова згода на оприлюднення»."
    )


def _page_placeholder_annotations() -> dict:
    """Повертає словник SQL-анотацій для визначення наявності текстів-заглушок макета без N+1 запитів."""
    block_q = Q()
    lead_q = Q()
    for phrase in PAGE_PLACEHOLDER_PHRASES:
        block_q |= Q(text__icontains=phrase) | Q(data_text__icontains=phrase)
        lead_q |= Q(lead__icontains=phrase)

    block_subquery = (
        Block.objects.filter(page=OuterRef("pk"))
        .annotate(data_text=Cast("data", output_field=TextField()))
        .filter(block_q)
    )
    return {
        "_has_block_placeholder": Exists(block_subquery),
        "_has_placeholder": Case(
            When(lead_q | Q(_has_block_placeholder=True), then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    }


class PageHasPlaceholderFilter(admin.SimpleListFilter):
    """Фільтр сторінок за наявністю текстів-заглушок із початкового макета."""

    title = "Містить заглушку"
    parameter_name = "has_placeholder"

    def lookups(self, request, model_admin):
        return (
            ("yes", "Є заглушка"),
            ("no", "Без заглушок"),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if val not in ("yes", "no"):
            return queryset
        if not hasattr(queryset.query, "annotations") or "_has_placeholder" not in queryset.query.annotations:
            queryset = queryset.annotate(**_page_placeholder_annotations())
        if val == "yes":
            return queryset.filter(_has_placeholder=True)
        return queryset.filter(_has_placeholder=False)


# --- 1. Сторінки сайту (Page) ---
@admin.register(Page)
class PageAdmin(SimpleHistoryAdmin):
    list_display = (
        "title",
        "section",
        "publication_status",
        "missing_docs_display",
        "has_placeholder_display",
        "updated_at",
        "order",
    )
    list_filter = ("section", "is_published", PageHasPlaceholderFilter)
    search_fields = ("title", "lead", "slug")
    list_select_related = ("section",)
    prepopulated_fields = {"slug": ("title",)}
    inlines = [BlockInline, DocumentInline, PersonInline]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(
            _missing_docs_count=Count(
                "documents",
                filter=Q(documents__file="") | Q(documents__file__isnull=True),
                distinct=True,
            ),
            **_page_placeholder_annotations(),
        )

    fieldsets = (
        (
            "Основна інформація про сторінку",
            {
                "fields": (
                    "section",
                    "title",
                    "slug",
                    "lead",
                    ("is_published", "docs_first"),
                    "order",
                ),
                "description": "Основні налаштування сторінки. Нижче у згорнутих блоках редагуються тексти, документи та працівники.",
            },
        ),
        (
            "Налаштування для пошукових систем (необов'язково)",
            {
                "classes": ("collapse",),
                "fields": ("meta_description",),
            },
        ),
    )

    class Media:
        css = {"all": ("core/admin/admin.css",)}
        js = ("core/admin/block_form.js",)

    @admin.display(description="Статус публікації", ordering="is_published")
    def publication_status(self, obj: Page):
        if obj.is_published:
            return format_html(
                '<span class="oberih-badge oberih-badge--published">Опубліковано</span>'
            )
        return format_html(
            '<span class="oberih-badge oberih-badge--draft">Чернетка</span>'
        )

    @admin.display(description="Документів без файлу", ordering="_missing_docs_count")
    def missing_docs_display(self, obj: Page):
        count = getattr(obj, "_missing_docs_count", 0)
        if count > 0:
            return format_html(
                '<span style="color:#92400e;font-weight:700;">{}</span>',
                count,
            )
        return "—"

    @admin.display(description="Містить заглушку", ordering="_has_placeholder")
    def has_placeholder_display(self, obj: Page):
        if getattr(obj, "_has_placeholder", False):
            return format_html(
                '<span style="color:#92400e;font-weight:700;">Так</span>'
            )
        return "—"

    def delete_view(self, request, object_id, extra_context=None):
        extra_context = extra_context or {}
        extra_context["title"] = (
            "Ви впевнені, що хочете видалити цю сторінку? "
            "Разом із нею будуть видалені всі її текстові блоки, документи та картки працівників."
        )
        messages.warning(
            request,
            "Увага! Видалення сторінки призведе до видалення всіх прикріплених до неї блоків, документів і карток працівників. "
            "Якщо сторінку потрібно лише тимчасово сховати з сайту, поверніться назад і зніміть галочку «Опубліковано».",
        )
        return super().delete_view(request, object_id, extra_context=extra_context)


# --- 2. Розділи меню (Section) ---
@admin.register(Section)
class SectionAdmin(SimpleHistoryAdmin):
    list_display = ("title", "order", "show_in_menu", "color", "slug")
    list_editable = ("order", "show_in_menu")
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ("title", "lead")


# --- 3. Новини (NewsItem) ---
@admin.register(NewsItem)
class NewsItemAdmin(SimpleHistoryAdmin):
    list_display = (
        "title",
        "category",
        "published_date",
        "publication_status",
        "is_published",
    )
    list_editable = ("is_published",)
    list_filter = ("is_published", "category", "published_date")
    search_fields = ("title", "summary", "body")
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "published_date"

    fieldsets = (
        (
            "Публікація новини",
            {
                "fields": (
                    "title",
                    "slug",
                    ("category", "accent_color"),
                    ("published_date", "is_published"),
                    "summary",
                    "body",
                    "photo",
                )
            },
        ),
    )

    class Media:
        css = {"all": ("core/admin/admin.css",)}

    @admin.display(description="Статус", ordering="is_published")
    def publication_status(self, obj: NewsItem):
        if obj.is_published:
            return format_html(
                '<span class="oberih-badge oberih-badge--published">Опубліковано</span>'
            )
        return format_html(
            '<span class="oberih-badge oberih-badge--draft">Чернетка</span>'
        )


# --- 4. Звернення з форми зворотного зв'язку (ContactMessage) ---
@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "name", "email", "is_read")
    list_editable = ("is_read",)
    list_filter = ("is_read", "created_at")
    search_fields = ("name", "email", "message")
    ordering = ("-created_at", "-id")
    readonly_fields = ("created_at", "name", "email", "message", "consent_given")
    fields = ("created_at", "name", "email", "message", "consent_given", "is_read")
    actions = ["mark_as_read"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return True

    @admin.action(description="Позначити вибрані звернення як прочитані")
    def mark_as_read(self, request, queryset):
        updated = queryset.update(is_read=True)
        self.message_user(
            request,
            f"Позначено як прочитані: {updated} звернень.",
            messages.SUCCESS,
        )


# --- 5. Працівники (Person) ---
@admin.register(Person)
class PersonAdmin(SimpleHistoryAdmin):
    list_display = (
        "position",
        "full_name_display",
        "page",
        "consent_icon",
        "order",
    )
    list_editable = ("order",)
    list_filter = ("consent_given", "page__section", "page")
    search_fields = ("full_name", "position", "page__title")
    list_select_related = ("page", "page__section")
    fields = ("page", "position", "full_name", "photo", "consent_given", "order")

    class Media:
        css = {"all": ("core/admin/admin.css",)}

    @admin.display(description="ПІБ", ordering="full_name")
    def full_name_display(self, obj: Person):
        if obj.full_name:
            return obj.full_name
        return format_html('<span style="color:#6b7280;font-style:italic;">— не заповнено —</span>')

    @admin.display(description="Згода на оприлюднення", ordering="consent_given")
    def consent_icon(self, obj: Person):
        if obj.consent_given:
            return format_html(
                '<span class="oberih-consent-yes" title="Згода є — показується на сайті">✔ Є згода</span>'
            )
        return format_html(
            '<span class="oberih-consent-no" title="Без згоди — приховано на сайті">✘ Немає згоди</span>'
        )


class DocumentFileStatusFilter(admin.SimpleListFilter):
    """Фільтр документів за наявністю прикріпленого PDF-файлу."""

    title = "Наявність файлу"
    parameter_name = "has_file"

    def lookups(self, request, model_admin):
        return (
            ("no", "Без файлу"),
            ("yes", "Із завантаженим файлом"),
        )

    def queryset(self, request, queryset):
        val = self.value()
        empty_q = Q(file="") | Q(file__isnull=True)
        if val == "no":
            return queryset.filter(empty_q)
        if val == "yes":
            return queryset.exclude(empty_q)
        return queryset


# --- 6. Документи (Document) ---
@admin.register(Document)
class DocumentAdmin(SimpleHistoryAdmin):
    list_display = (
        "title",
        "page",
        "file_status_display",
        "show_st30_notice",
        "order",
    )
    list_editable = ("order", "show_st30_notice")
    list_filter = (
        DocumentFileStatusFilter,
        "show_st30_notice",
        "page__section",
        "page",
    )
    search_fields = ("title", "page__title")
    list_select_related = ("page", "page__section")
    fields = ("page", "title", "file", "order", "show_st30_notice")

    @admin.display(description="Файл")
    def file_status_display(self, obj: Document):
        if not obj.has_file:
            return format_html(
                '<span style="color:#92400e;font-weight:600;">✘ Без файлу</span>'
            )
        try:
            size_bytes = obj.file.size
            if size_bytes < 1024 * 1024:
                size_str = f"{size_bytes / 1024:.1f} КБ"
            else:
                size_str = f"{size_bytes / (1024 * 1024):.2f} МБ"
            return format_html(
                '<span style="color:#15803d;font-weight:600;">✔ Завантажено ({})</span>',
                size_str,
            )
        except OSError:
            return format_html(
                '<span style="color:#15803d;font-weight:600;">✔ Завантажено</span>'
            )


# --- 7. Головна сторінка (HomePage) ---
@admin.register(HomePage)
class HomePageAdmin(SingletonModelAdmin):
    fieldsets = (
        (
            "1. Перший екран (Героїчний блок)",
            {
                "fields": (
                    "hero_eyebrow",
                    "hero_title",
                    "hero_title_em",
                    "hero_lead",
                    ("hero_fact_1", "hero_fact_2", "hero_fact_3"),
                ),
            },
        ),
        (
            "2. Блок «Про НРЦ «Оберіг»»",
            {
                "fields": (
                    "about_eyebrow",
                    "about_title",
                    "about_text",
                    "about_quote",
                    "about_quote_note",
                ),
            },
        ),
        (
            "3. Блок «Новини»",
            {
                "fields": ("news_eyebrow", "news_title"),
            },
        ),
        (
            "4. Блок «Документи»",
            {
                "fields": ("docs_eyebrow", "docs_title", "docs_note"),
            },
        ),
        (
            "5. Блок «Безпека та захист»",
            {
                "fields": ("safety_eyebrow", "safety_title", "safety_text"),
            },
        ),
        (
            "6. Блок «Запрошуємо»",
            {
                "fields": (
                    "welcome_eyebrow",
                    "welcome_title",
                    "welcome_text",
                    "welcome_image",
                ),
            },
        ),
        (
            "7. Блок «Наші спонсори»",
            {
                "fields": (
                    "sponsors_eyebrow",
                    "sponsors_title",
                    "sponsors_text",
                    "sponsors_note",
                ),
            },
        ),
    )


# --- 8. Налаштування сайту (SiteSettings) ---
@admin.register(SiteSettings)
class SiteSettingsAdmin(SingletonModelAdmin):
    fieldsets = (
        (
            "Контакти та реквізити закладу (відображаються на всіх сторінках)",
            {
                "fields": (
                    "phone",
                    "email",
                    "address",
                    "director_name",
                    "map_url",
                    "footer_text",
                    "schedule_notice",
                    "privacy_policy_page",
                ),
            },
        ),
    )


# --- 9. Спонсори (Sponsor) ---
@admin.register(Sponsor)
class SponsorAdmin(SimpleHistoryAdmin):
    list_display = ("name", "logo_status", "url", "order")
    list_editable = ("order",)
    search_fields = ("name", "url")
    fields = ("name", "logo", "url", "order")

    @admin.display(description="Логотип")
    def logo_status(self, obj: Sponsor):
        if obj.has_logo:
            return format_html(
                '<span style="color:#15803d;font-weight:600;">✔ Завантажено</span>'
            )
        return format_html(
            '<span style="color:#92400e;">Ще не завантажено</span>'
        )
