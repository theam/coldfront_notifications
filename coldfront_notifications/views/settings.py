from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required as staff_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..forms import NotificationVariableForm, SenderConfigForm
from ..models import NotificationTemplate, NotificationVariable, SenderConfig


@staff_required
def variable_list(request):
    return render(request, "variable_list.html", {
        "variables": NotificationVariable.objects.filter(is_deleted=False),
    })


@staff_required
def variable_form(request, pk=None):
    instance = get_object_or_404(NotificationVariable, pk=pk) if pk else None
    if request.method == "POST":
        form = NotificationVariableForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            action_word = "updated" if instance else "created"
            messages.success(request, f'Variable "{{{{{obj.key}}}}}" {action_word}.')
            return redirect("notifications:variable-list")
    else:
        form = NotificationVariableForm(instance=instance)

    from ..resolvers import QUERY_CHOICES
    return render(request, "variable_form.html", {
        "form":     form,
        "variable": instance,
        "query_choices": QUERY_CHOICES,
    })


@staff_required
def variable_delete(request, pk):
    instance = get_object_or_404(NotificationVariable, pk=pk)
    affected = [
        t for t in NotificationTemplate.objects.filter(is_deleted=False)
        if instance.key in t.variables
    ]
    if request.method == "POST":
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted"])
        messages.success(request, f'Variable "{{{{{instance.key}}}}}" deleted.')
        return redirect("notifications:variable-list")
    return render(request, "variable_delete_confirm.html", {
        "variable": instance,
        "affected_templates": affected,
    })


@staff_required
def variables_view(request):
    """Return the full catalog of NotificationVariables as JSON."""
    return JsonResponse({
        "variables": [
            {
                "key":          v.key,
                "label":        v.label,
                "description":  v.description,
                "example":      v.example,
                "source":       v.source,
                "input_widget": v.input_widget,
                "is_required":  v.is_required,
            }
            for v in NotificationVariable.objects.filter(is_deleted=False)
        ]
    })


@staff_required
def settings_view(request):
    senders = SenderConfig.objects.all()
    return render(request, "settings.html", {"senders": senders})


@staff_required
def sender_form(request, pk=None):
    instance = get_object_or_404(SenderConfig, pk=pk) if pk else None
    if request.method == "POST":
        form = SenderConfigForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            action_word = "updated" if instance else "added"
            messages.success(request, f'Address "{obj.email}" {action_word}.')
            return redirect("notifications:settings")
    else:
        form = SenderConfigForm(instance=instance)
    return render(request, "sender_form.html", {
        "form": form,
        "sender": instance,
    })


@staff_required
@require_POST
def sender_delete(request, pk):
    obj = get_object_or_404(SenderConfig, pk=pk)
    email = obj.email
    obj.delete()
    messages.success(request, f'Address "{email}" removed.')
    return redirect("notifications:settings")
