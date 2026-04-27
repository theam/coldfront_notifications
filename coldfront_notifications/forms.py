from django import forms
from django.utils.text import slugify
from .models import NotificationTemplate, NotificationVariable, SenderConfig
from .resolvers import QUERY_CHOICES


class NotificationVariableForm(forms.ModelForm):
    """Plugin-side form (Bootstrap-themed). Restricts resolver_key to the
    whitelisted QUERY_CHOICES so admins can't invent arbitrary ORM paths."""

    resolver_key = forms.ChoiceField(
        required=False,
        choices=[("", "— choose a query source —")]
               + [(k, f"{group}: {label}") for (k, label, group) in QUERY_CHOICES],
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = NotificationVariable
        fields = [
            "key", "label", "description", "example",
            "source", "resolver_key",
            "input_widget", "value", "is_required",
        ]
        widgets = {
            "key":          forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. pi_full_name"}),
            "label":        forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. PI Full Name"}),
            "description":  forms.TextInput(attrs={"class": "form-control"}),
            "example":      forms.TextInput(attrs={"class": "form-control"}),
            "source":       forms.RadioSelect(),
            "input_widget": forms.Select(attrs={"class": "form-control"}),
            "value":        forms.Textarea(attrs={"class": "form-control", "rows": 2,
                                                  "placeholder": "Fixed value used in every campaign"}),
        }

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source")
        if source == NotificationVariable.SOURCE_MANUAL:
            cleaned["resolver_key"] = ""
            required = cleaned.get("is_required", True)
            if required and not (cleaned.get("value") or "").strip():
                self.add_error("value", "Required when source is Manual.")
        elif source == NotificationVariable.SOURCE_QUERY:
            cleaned["value"] = ""
            if not cleaned.get("resolver_key"):
                self.add_error("resolver_key", "Required when source is Query.")
        return cleaned


class SenderConfigForm(forms.ModelForm):
    class Meta:
        model = SenderConfig
        fields = ["label", "email", "is_default", "is_reply_to"]
        widgets = {
            "label": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. RC Help"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "e.g. rchelp@rc.fas.harvard.edu"}),
        }
        labels = {
            "is_default":  "Default From",
            "is_reply_to": "Default Reply-To",
        }
        help_texts = {
            "is_default":  "Pre-selected in the From dropdown on compose.",
            "is_reply_to": "Pre-selected in the Reply-To dropdown on compose.",
        }


class NotificationTemplateForm(forms.ModelForm):
    class Meta:
        model = NotificationTemplate
        fields = ["name", "subject", "body"]
        widgets = {
            "name":    forms.TextInput(attrs={"class": "form-control"}),
            "subject": forms.TextInput(attrs={"class": "form-control"}),
            "body":    forms.Textarea(attrs={"class": "form-control", "rows": 16}),
        }

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.slug:
            obj.slug = slugify(obj.name)
        if commit:
            obj.save()
        return obj


class ComposeForm(forms.Form):
    template_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    subject     = forms.CharField(
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    body = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 12}),
    )
    sender = forms.EmailField(
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    reply_to = forms.EmailField(
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    # recipient filters — all MultipleChoiceField, choices injected in __init__
    filter_projects   = forms.MultipleChoiceField(required=False)
    filter_allocations= forms.MultipleChoiceField(required=False)
    filter_departments= forms.MultipleChoiceField(required=False)
    filter_resources  = forms.MultipleChoiceField(required=False)
    filter_statuses   = forms.MultipleChoiceField(required=False)
    filter_roles      = forms.MultipleChoiceField(required=False)
    extra_recipients  = forms.CharField(required=False, widget=forms.Textarea())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Populate sender choices from DB
        senders = SenderConfig.objects.filter(is_reply_to=False)
        self.fields["sender"].widget = forms.Select(
            choices=[(s.email, f"{s.label} <{s.email}>") for s in senders],
            attrs={"class": "form-control"},
        )
        reply_tos = SenderConfig.objects.filter(is_reply_to=True)
        self.fields["reply_to"].widget = forms.Select(
            choices=[("", "—")] + [(s.email, f"{s.label} <{s.email}>") for s in reply_tos],
            attrs={"class": "form-control"},
        )

    def get_filters(self) -> dict:
        return {
            "projects":    self.cleaned_data.get("filter_projects", []),
            "allocations": self.cleaned_data.get("filter_allocations", []),
            "departments": self.cleaned_data.get("filter_departments", []),
            "resources":   self.cleaned_data.get("filter_resources", []),
            "statuses":    self.cleaned_data.get("filter_statuses", []),
            "roles":       self.cleaned_data.get("filter_roles", []),
        }

    def get_extra_recipients(self) -> list[str]:
        raw = self.cleaned_data.get("extra_recipients", "")
        return [line.strip() for line in raw.splitlines() if line.strip()]
