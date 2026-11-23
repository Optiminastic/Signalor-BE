"""
Google Business Profile in Agent mode: detection, the owner's draft, photos,
and the suggested description.

Run: python manage.py test apps.growth_agent.tests.test_business_profile --settings=config.settings.test
"""

import io
import time
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image

from ..auth import sign_request
from ..models import BusinessProfileCheck, BusinessProfilePhoto
from ..services.business_profile import detect, photos, profile
from .test_api import OTHER_EMAIL, PRO_EMAIL, SECRET
from .test_execution_flow import ExecutionTestCase

S3 = {
    "GBP_S3_BUCKET": "photos",
    "GBP_S3_ACCESS_KEY_ID": "id",
    "GBP_S3_SECRET_ACCESS_KEY": "secret",
}


def image(width=400, height=300, fmt="PNG") -> bytes:
    buffer = io.BytesIO()
    # Noise keeps the file above the 10 KB minimum.
    Image.effect_noise((width, height), 80).convert("RGB").save(buffer, format=fmt)
    return buffer.getvalue()


class MatchTests(SimpleTestCase):
    PLACES = [
        {"title": "Acme Widgets", "address": "1 Main St, Pune", "website": "https://other.com", "cid": "1"},
        {
            "title": "Brand Store",
            "address": "22 MG Road, Bengaluru",
            "website": "https://www.brand.com/",
            "cid": "42",
        },
    ]

    def test_a_listing_with_the_brands_website_is_found(self):
        found = detect.match(self.PLACES, brand="Brand", domain="brand.com")
        self.assertEqual((found.status, found.matched_by), ("found", "website"))
        self.assertEqual(found.place["link"], "https://www.google.com/maps?cid=42")

    def test_name_and_address_match_when_the_website_is_missing(self):
        places = [{"title": "Brand Store", "address": "22 MG Road, Bengaluru", "cid": "7"}]
        found = detect.match(places, brand="Brand Store", domain="brand.com", address="22 MG Road Bengaluru")
        self.assertEqual((found.status, found.matched_by), ("found", "name_address"))

    def test_a_name_alone_is_not_enough(self):
        places = [{"title": "Brand Store", "address": "Somewhere else", "cid": "7"}]
        self.assertEqual(detect.match(places, brand="Brand Store", domain="brand.com").status, "not_found")

    def test_a_failed_search_is_unknown_never_not_found(self):
        with mock.patch.object(detect, "_places", return_value=None):
            self.assertEqual(detect.detect(brand="Brand", website="https://brand.com").status, "unknown")


class PhotoCheckTests(SimpleTestCase):
    def test_a_good_png_passes(self):
        self.assertEqual(photos.inspect(image())[:2], ("image/png", "png"))

    def test_small_or_odd_files_are_refused(self):
        with self.assertRaises(photos.PhotoError):
            photos.inspect(b"x" * 100)  # under 10 KB
        with self.assertRaises(photos.PhotoError):
            photos.inspect(b"not an image" * 2000)
        with self.assertRaises(photos.PhotoError):
            photos.inspect(image(width=200, height=600))  # under 250 px on one side
        with self.assertRaises(photos.PhotoError):
            photos.inspect(image(fmt="GIF"))


class BusinessProfileApiTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        self.base = f"/api/growth/programs/{self.pro_org.slug}/business-profile/"
        self._body = {}

    def test_before_any_check_the_tab_says_so(self):
        body = self.signed("GET", self.base, PRO_EMAIL).json()
        self.assertEqual((body["status"], body["listing"], body["photos"]), ("never_checked", None, []))

    def test_a_check_records_the_listing(self):
        found = detect.Detection(
            "found", {"name": "Brand", "link": "https://www.google.com/maps?cid=1"}, "website"
        )
        with mock.patch.object(detect, "detect", return_value=found):
            profile.run_check(self.program)
        body = self.signed("GET", self.base, PRO_EMAIL).json()
        self.assertEqual((body["status"], body["listing"]["name"]), ("found", "Brand"))
        self.assertEqual(BusinessProfileCheck.objects.count(), 1)

    def test_check_now_runs_at_most_once_an_hour(self):
        with mock.patch("apps.growth_agent.analysis_tasks.dispatch_business_profile_check"):
            with self.captureOnCommitCallbacks(execute=True):
                first = self.signed("POST", f"{self.base}check/", PRO_EMAIL)
                second = self.signed("POST", f"{self.base}check/", PRO_EMAIL)
        self.assertEqual((first.status_code, second.status_code), (202, 200))

    def test_the_draft_is_saved_and_validated(self):
        self._body = {"name": "Brand Store", "phone": "+91 98765 43210", "hours": {"mon": "09:00-18:00"}}
        self.assertEqual(self.signed("PATCH", f"{self.base}draft/", PRO_EMAIL).status_code, 200)
        self._body = {"description": "x" * 751}
        self.assertEqual(self.signed("PATCH", f"{self.base}draft/", PRO_EMAIL).status_code, 400)
        self._body = {"hours": {"someday": "9-5"}}
        self.assertEqual(self.signed("PATCH", f"{self.base}draft/", PRO_EMAIL).status_code, 400)
        draft = self.signed("GET", self.base, PRO_EMAIL).json()["draft"]
        self.assertEqual(draft["name"], "Brand Store")

    def test_suggestions_come_from_the_model_and_are_trimmed(self):
        answer = profile.ListingSuggestion(
            description="y" * 900, categories=["Store", "Shop", "A", "B", "C", "D"]
        )
        with mock.patch.object(profile, "ask_structured", return_value=answer):
            body = self.signed("POST", f"{self.base}suggest/", PRO_EMAIL).json()
        self.assertEqual((len(body["description"]), len(body["categories"])), (750, 5))

    def test_another_brands_owner_gets_nothing(self):
        for method, path in (("GET", ""), ("POST", "check/"), ("PATCH", "draft/"), ("POST", "suggest/")):
            with self.subTest(path=path):
                self.assertIn(self.signed(method, f"{self.base}{path}", OTHER_EMAIL).status_code, (403, 404))


@override_settings(**S3)
class PhotoStorageTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        self.client_mock = mock.MagicMock()
        self.client_mock.generate_presigned_url.return_value = "https://signed.example/photo"
        patcher = mock.patch.object(photos, "_client", return_value=self.client_mock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_photo_is_stored_privately_under_the_brand(self):
        photo = photos.save(self.program, image(), BusinessProfilePhoto.Kind.OTHER)
        args = self.client_mock.put_object.call_args.kwargs
        self.assertTrue(args["Key"].startswith(f"gbp/{self.pro_org.pk}/"))
        self.assertEqual((args["ContentType"], photo.width, photo.height), ("image/png", 400, 300))

    def test_a_new_logo_replaces_the_old_one(self):
        first = photos.save(self.program, image(), BusinessProfilePhoto.Kind.LOGO)
        photos.save(self.program, image(), BusinessProfilePhoto.Kind.LOGO)
        self.assertEqual(self.program.business_profile_photos.filter(kind="logo").count(), 1)
        self.client_mock.delete_object.assert_called_once_with(Bucket="photos", Key=first.storage_key)

    def test_at_most_ten_other_photos(self):
        for _ in range(photos.MAX_OTHER):
            photos.save(self.program, image(), BusinessProfilePhoto.Kind.OTHER)
        with self.assertRaises(photos.PhotoError):
            photos.save(self.program, image(), BusinessProfilePhoto.Kind.OTHER)

    def test_upload_and_delete_through_the_api(self):
        base = f"/api/growth/programs/{self.pro_org.slug}/business-profile/photos/"
        upload = SimpleUploadedFile("logo.png", image(), content_type="image/png")
        response = self.signed_multipart(base, {"file": upload, "kind": "logo"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["photos"][0]["url"], "https://signed.example/photo")
        photo_id = response.json()["photos"][0]["id"]
        self._body = {}
        self.assertEqual(self.signed("DELETE", f"{base}{photo_id}/", PRO_EMAIL).status_code, 200)
        self.assertFalse(BusinessProfilePhoto.objects.exists())

    def signed_multipart(self, path, data):
        """A signed multipart POST, as the Next.js proxy sends uploads."""
        ts = str(int(time.time()))
        sig = sign_request(SECRET, ts, PRO_EMAIL, "POST", path)
        return self.client.post(
            path, data, HTTP_X_GROWTH_EMAIL=PRO_EMAIL, HTTP_X_GROWTH_TIMESTAMP=ts, HTTP_X_GROWTH_SIGNATURE=sig
        )


class StorageNotSetUpTests(ExecutionTestCase):
    @override_settings(GBP_S3_BUCKET="")
    def test_uploads_say_storage_isnt_set_up(self):
        with self.assertRaises(photos.PhotoError) as caught:
            photos.save(self.program, image(), BusinessProfilePhoto.Kind.OTHER)
        self.assertEqual(caught.exception.status, 503)
