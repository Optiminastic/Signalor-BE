"""Google Business Profile tab in Agent mode.

GET    programs/<slug>/business-profile/              -> latest check, draft, photos
POST   programs/<slug>/business-profile/check/        -> search Google for the listing now (once an hour)
PATCH  programs/<slug>/business-profile/draft/        -> save the owner's facts for a new listing
POST   programs/<slug>/business-profile/suggest/      -> the agent's suggested description and categories
POST   programs/<slug>/business-profile/photos/       -> upload one photo (multipart: file, kind)
DELETE programs/<slug>/business-profile/photos/<id>/  -> remove a photo

Owner-only (the program lookup is scoped to the signed-in owner).
"""

from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from .models import BusinessProfilePhoto
from .services.business_profile import photos, profile
from .views import GrowthBaseView
from .views_control import _program_or_404

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class DraftSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, allow_blank=True, required=False)
    primary_category = serializers.CharField(max_length=100, allow_blank=True, required=False)
    extra_categories = serializers.ListField(
        child=serializers.CharField(max_length=100), max_length=profile.MAX_EXTRA_CATEGORIES, required=False
    )
    address = serializers.CharField(max_length=300, allow_blank=True, required=False)
    hide_address = serializers.BooleanField(required=False)  # a service-area business
    service_area = serializers.CharField(max_length=300, allow_blank=True, required=False)
    phone = serializers.RegexField(r"^[+0-9 ()\-]{6,25}$", allow_blank=True, required=False)
    website = serializers.URLField(max_length=300, allow_blank=True, required=False)
    hours = serializers.DictField(
        child=serializers.CharField(max_length=40, allow_blank=True), required=False
    )
    description = serializers.CharField(max_length=profile.DESCRIPTION_MAX, allow_blank=True, required=False)

    def validate_hours(self, value: dict) -> dict:
        unknown = set(value) - set(DAYS)
        if unknown:
            raise serializers.ValidationError(f"Unknown days: {', '.join(sorted(unknown))}.")
        return value


class BusinessProfileView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        return Response(profile.report(program))


class BusinessProfileCheckView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        started = profile.start(program, guard=profile.START_GUARD, actor_email=request.user.email)
        code = status.HTTP_202_ACCEPTED if started else status.HTTP_200_OK
        return Response({**profile.report(program), "started": started}, status=code)


class BusinessProfileDraftView(GrowthBaseView):
    def patch(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        payload = DraftSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        draft = profile.draft(program)
        draft.fields = {**(draft.fields or {}), **payload.validated_data}
        draft.save(update_fields=["fields", "updated_at"])
        return Response(profile.report(program))


class BusinessProfileSuggestView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        return Response(profile.suggest(program))


class BusinessProfilePhotoListView(GrowthBaseView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        upload = request.FILES.get("file")
        kind = request.data.get("kind", BusinessProfilePhoto.Kind.OTHER)
        if upload is None or kind not in BusinessProfilePhoto.Kind.values:
            return Response(
                {"detail": "Send one photo as 'file' and a 'kind'."}, status=status.HTTP_400_BAD_REQUEST
            )
        if upload.size > photos.MAX_BYTES:
            return Response({"detail": "That photo is over 5 MB."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            photos.save(program, upload.read(), kind)
        except photos.PhotoError as exc:
            return Response({"detail": str(exc)}, status=exc.status)
        return Response(profile.report(program), status=status.HTTP_201_CREATED)


class BusinessProfilePhotoView(GrowthBaseView):
    def delete(self, request, org_slug: str, photo_id: int):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        photo = program.business_profile_photos.filter(pk=photo_id).first()
        if photo is None:
            return Response({"detail": "Photo not found."}, status=status.HTTP_404_NOT_FOUND)
        photos.delete(photo)
        return Response(profile.report(program))
