from django import forms

from .models import NOTE_LIMIT, Ticket


class RequiredReportText(forms.CharField):
    def __init__(self, **kwargs):
        super().__init__(strip=False, **kwargs)

    def validate(self, value):
        super().validate(value)
        if value and not value.strip():
            raise forms.ValidationError(self.error_messages['required'], code='required')


class TicketSubmissionForm(forms.ModelForm):
    submission_id = forms.UUIDField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Ticket
        fields = ['device_name', 'location', 'description']
        field_classes = dict.fromkeys(fields, RequiredReportText)
        widgets = {
            'device_name': forms.TextInput(),
            'location': forms.TextInput(),
            'description': forms.Textarea(attrs={'rows': 4}),
        }


class TicketVersionForm(forms.Form):
    version = forms.IntegerField(min_value=1, max_value=2**63 - 2, widget=forms.HiddenInput)


class TicketNoteForm(TicketVersionForm):
    content = RequiredReportText(max_length=NOTE_LIMIT)
