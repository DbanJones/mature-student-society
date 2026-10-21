"""Member-facing forms. All persisted changes go through the domain services."""

from django import forms
from django.utils import timezone

from accounts.models import COLLEGES


COLLEGE_CHOICES = [(key, name) for key, name in COLLEGES if key != "other"]
DATETIME_FORMAT = "%Y-%m-%dT%H:%M"
INVITATIONS = [("open", "No obligation — guests do not need to host me"), ("reciprocal", "Swap — guests promise to host me in return")]
BOOKING_MODES = [("approval", "I choose from members who request a place"), ("instant", "Members can reserve an eligible place straight away")]


def datetime_field(label, **kwargs):
    return forms.DateTimeField(
        label=label, input_formats=[DATETIME_FORMAT],
        widget=forms.DateTimeInput(format=DATETIME_FORMAT, attrs={"type": "datetime-local"}),
        **kwargs,
    )


class BrowseForm(forms.Form):
    college = forms.ChoiceField(choices=[("", "All colleges")] + COLLEGE_CHOICES, required=False)
    invitation_type = forms.ChoiceField(label="Invitation", choices=[("", "Any invitation"), ("open", "No obligation"), ("reciprocal", "Swap")], required=False)
    booking_mode = forms.ChoiceField(label="Booking", choices=[("", "Any booking option"), ("approval", "Host chooses"), ("instant", "Reserve immediately")], required=False)
    after = forms.DateField(label="From date", widget=forms.DateInput(attrs={"type": "date"}), required=False)
    before = forms.DateField(label="Until date", widget=forms.DateInput(attrs={"type": "date"}), required=False)
    min_places = forms.TypedChoiceField(label="Places needed", choices=[(1, "1 or more"), (2, "2 or more"), (3, "3 places")], coerce=int, empty_value=1, required=False, initial=1)

    def clean(self):
        data = super().clean()
        if data.get("after") and data.get("before") and data["before"] < data["after"]:
            self.add_error("before", "Choose a date on or after the first date.")
        return data


class AdditionalDateForm(forms.Form):
    date = forms.DateField(label="Another date", widget=forms.DateInput(attrs={"type": "date"}))


AdditionalDatesFormSet = forms.formset_factory(AdditionalDateForm, extra=3, max_num=19, validate_max=True, absolute_max=19)


class ListingForm(forms.Form):
    title = forms.CharField(max_length=140, label="Formal name")
    college = forms.ChoiceField(choices=COLLEGE_CHOICES, help_text="Your profile college. Update your profile first if your college has changed.")
    description = forms.CharField(required=False, max_length=6000, widget=forms.Textarea(attrs={"rows": 3}), label="About the dinner", help_text="Anything else you would like guests to know.")
    location = forms.CharField(max_length=200, label="Meeting place", help_text="For example, the porters' lodge. Keep contact details out of this description.")
    start = datetime_field("Formal starts")
    end = datetime_field("Formal ends", required=False)
    capacity = forms.IntegerField(min_value=1, max_value=3, initial=3, label="Guest places offered", help_text="Only include places you can offer here, after allowing for any guests arranged elsewhere. Your college may allow fewer than three.")
    booking_closes_at = datetime_field("Reservations close", help_text="Allow time to arrange the college's tickets before the formal.")
    price_text = forms.CharField(max_length=160, label="Cost and payment", help_text="For example, £18 paid to the host by bank transfer. Include the cost even if no return invitation is expected.")
    practical_details = forms.CharField(required=False, max_length=4000, widget=forms.Textarea(attrs={"rows": 3}), label="Dress code, access and other arrangements", help_text="Include dietary arrangements and ticket collection if known. Visible to members.")
    invitation_type = forms.ChoiceField(choices=INVITATIONS, widget=forms.RadioSelect, initial="open", label="What are you offering?")
    booking_mode = forms.ChoiceField(choices=BOOKING_MODES, widget=forms.RadioSelect, initial="approval", label="How can members get a place?")
    allowed_return_colleges = forms.MultipleChoiceField(choices=COLLEGE_CHOICES, required=False, widget=forms.CheckboxSelectMultiple, label="Choose return colleges", help_text="Leave all unchecked to accept any college. Select colleges to accept swaps only from members who can host there.")
    return_by = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Return dinner must take place by", help_text="For swaps only. Choose a deadline for the dinner itself. Guests can choose their actual date later.")
    return_places = forms.TypedChoiceField(
        choices=[(0, "Match the number booked"), (1, "1 place per booking"), (2, "2 places per booking"), (3, "3 places per booking")],
        coerce=int, empty_value=0, required=False, initial=0,
        label="Places I want in return",
        help_text="Choose what each booking owes you, even if they book a different number of places.",
    )
    hosting_confirmed = forms.BooleanField(label="I am entitled to host at this college and have checked its guest limit, booking rules and ticket availability.")

    def __init__(self, *args, user, listing=None, **kwargs):
        initial = kwargs.pop("initial", {})
        if listing:
            for name in ("college", "capacity", "invitation_type", "booking_mode", "allowed_return_colleges", "booking_closes_at", "return_by", "return_places", "price_text", "practical_details"):
                initial[name] = getattr(listing, name)
            for name in ("title", "description", "location", "start", "end"):
                initial[name] = getattr(listing.event, name)
        else:
            initial.setdefault("college", user.college if user.college != "other" else "")
        super().__init__(*args, initial=initial, **kwargs)
        host = listing.host if listing else user
        self.fields["college"].choices = [(key, name) for key, name in COLLEGE_CHOICES if key == host.college]
        if not self.fields["college"].choices:
            self.fields["college"].choices = [("", "Add your college to your profile first")]

    def clean(self):
        data = super().clean()
        start, end, closes = data.get("start"), data.get("end"), data.get("booking_closes_at")
        now = timezone.now()
        if start and start <= now:
            self.add_error("start", "Choose a future formal.")
        if start and end and end <= start:
            self.add_error("end", "The end must be after the start.")
        if closes and closes <= now:
            self.add_error("booking_closes_at", "Reservations must close in the future.")
        if closes and start and closes >= start:
            self.add_error("booking_closes_at", "Close reservations before the formal starts.")
        if data.get("invitation_type") == "reciprocal":
            if not data.get("return_by"):
                self.add_error("return_by", "Choose a deadline for return invitations.")
            elif start and data["return_by"] <= timezone.localdate(start):
                self.add_error("return_by", "The return deadline must be after this formal.")
        else:
            data["allowed_return_colleges"] = []
            data["return_by"] = None
            data["return_places"] = 0
        return data


class ApplicationForm(forms.Form):
    terms_version = forms.IntegerField(widget=forms.HiddenInput, min_value=1)
    places = forms.TypedChoiceField(coerce=int, empty_value=1, required=False, initial=1, label="Places, including you")
    companion_names = forms.CharField(required=False, strip=False, max_length=220, widget=forms.Textarea(attrs={"rows": 2}), label="Other guests", help_text="One name per line. You can add these after booking.")
    return_college = forms.ChoiceField(choices=COLLEGE_CHOICES, required=False, label="College where I will host the return invitation")
    can_host = forms.BooleanField(required=False, label="I have permission to host a guest at my chosen return college.")
    accept_terms = forms.BooleanField(label="I agree to these invitation, cost and booking arrangements.")

    def __init__(self, *args, listing, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.listing = listing
        self.initial["terms_version"] = listing.terms_version
        self.initial["return_college"] = user.college
        # A repeated submission must still reach the service's idempotency check.
        # Fresh forms offer only currently available quantities.
        maximum = listing.capacity if self.is_bound else max(1, listing.spots_left)
        self.fields["places"].choices = [(n, f"{n} place{'s' if n != 1 else ''}") for n in range(1, maximum + 1)]
        if listing.invitation_type == "reciprocal":
            allowed = listing.allowed_return_colleges
            self.fields["return_college"].choices = [(key, name) for key, name in COLLEGE_CHOICES if key == user.college and (not allowed or key in allowed)]
            self.fields["return_college"].required = True
            self.fields["return_college"].widget = forms.HiddenInput()
            self.fields["can_host"].widget = forms.HiddenInput()
            self.fields["accept_terms"].label = f"I accept the cost and swap terms above, and can host at {user.get_college_display()}."
        else:
            del self.fields["return_college"]
            del self.fields["can_host"]

    def clean(self):
        data = super().clean()
        names = _guest_names(data.get("companion_names", ""))
        if any(len(name) > 100 for name in names):
            self.add_error("companion_names", "Keep each guest name under 100 characters.")
        elif len(names) > data.get("places", 1) - 1:
            self.add_error("companion_names", "Only add names for the other places you are booking.")
        else:
            data["companion_names"] = names
        if self.listing.invitation_type == "reciprocal":
            # The single visible agreement explicitly includes hosting permission.
            data["can_host"] = bool(data.get("accept_terms"))
        return data


class GuestNamesForm(forms.Form):
    companion_names = forms.CharField(required=False, strip=False, max_length=220, widget=forms.Textarea(attrs={"rows": 2}), label="Other guests", help_text="One name per line, in the same order as the guest places. Keep a blank line for a name you do not know yet.")

    def __init__(self, *args, booking, **kwargs):
        super().__init__(*args, **kwargs)
        self.booking = booking
        self.initial["companion_names"] = "\n".join(booking.companion_names)

    def clean_companion_names(self):
        names = _guest_names(self.cleaned_data["companion_names"])
        if len(names) > self.booking.places - 1 or any(len(name) > 100 for name in names):
            raise forms.ValidationError("Add one name per other guest place, up to 100 characters each.")
        return names


def _guest_names(value):
    names = [name.strip() for name in value.splitlines()]
    while names and not names[-1]:
        names.pop()
    return names


class AttendanceForm(forms.Form):
    attendance_submitted = forms.BooleanField(initial=True, widget=forms.HiddenInput())
    attended_places = forms.TypedMultipleChoiceField(coerce=int, required=False, widget=forms.CheckboxSelectMultiple, label="Who attended?")

    def __init__(self, *args, booking, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["attended_places"].choices = list(enumerate(booking.seat_names))
        previous = booking.guest_attendance if user.pk == booking.guest_id else booking.host_attendance
        self.initial["attended_places"] = previous if previous is not None else list(range(booking.places))
        self.fields["attended_places"].help_text = "Select everyone who attended. Leave all unchecked if nobody attended."


class ReasonForm(forms.Form):
    reason = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={"rows": 3}), label="Reason", required=False)


class IssueForm(ReasonForm):
    reason = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={"rows": 3}), label="What needs the committee's help?", help_text="This is visible to both members and the committee.")


class ReturnProposalForm(forms.Form):
    listing_id = forms.ChoiceField(label="Your return formal")

    def __init__(self, *args, listings, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["listing_id"].choices = [(listing.pk, f"{listing.event.title} — {timezone.localtime(listing.event.start):%d %b %Y, %H:%M} ({listing.spots_left} place(s) left)") for listing in listings]


class ExtensionForm(forms.Form):
    return_by = forms.DateField(label="Proposed return deadline", widget=forms.DateInput(attrs={"type": "date"}))


class ResolutionForm(forms.Form):
    resolution = forms.ChoiceField(choices=[("resume", "Resume the exchange"), ("waive", "Waive the return invitation"), ("close", "Close without a return obligation"), ("complete", "Confirm both invitations were fulfilled")])
    note = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={"rows": 3}), label="Reason for the decision", help_text="Recorded in the history and shared with both members.")
