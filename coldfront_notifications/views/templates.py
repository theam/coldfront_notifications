import json

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required as staff_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from ..forms import NotificationTemplateForm
from ..models import NotificationCampaign, NotificationTemplate, NotificationVariable


@staff_required
def template_list(request):
    return render(request, "template_list.html", {
        "templates": NotificationTemplate.objects.filter(is_deleted=False),
    })


@staff_required
def template_form(request, pk=None):
    instance = get_object_or_404(NotificationTemplate, pk=pk) if pk else None
    if request.method == "POST":
        form = NotificationTemplateForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            if not obj.slug:
                obj.slug = slugify(obj.name)
            obj.save()
            action_word = "updated" if instance else "created"
            messages.success(request, f'Template "{obj.name}" {action_word}.')
            return redirect("notifications:template-list")
    else:
        form = NotificationTemplateForm(instance=instance)

    variables_json = json.dumps([
        {
            "key":         v.key,
            "label":       v.label,
            "description": v.description,
            "example":     v.example,
            "source":      v.source,
        }
        for v in NotificationVariable.objects.filter(is_deleted=False)
    ])

    return render(request, "template_form.html", {
        "form":     form,
        "template": instance,
        "variables_json": variables_json,
    })


@staff_required
def template_delete(request, pk):
    instance = get_object_or_404(NotificationTemplate, pk=pk)
    notification_count = NotificationCampaign.objects.filter(template=instance).count()
    if request.method == "POST":
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted"])
        messages.success(request, f'Template "{instance.name}" deleted.')
        return redirect("notifications:template-list")
    return render(request, "template_delete_confirm.html", {
        "template": instance,
        "notification_count": notification_count,
    })


@staff_required
def template_json(request, pk):
    """Return a single template's live content for the compose-page sidebar."""
    t = get_object_or_404(NotificationTemplate, pk=pk)
    return JsonResponse({
        "id":        t.pk,
        "slug":      t.slug,
        "name":      t.name,
        "subject":   t.subject,
        "body":      t.body,
        "variables": t.variables,
    })
