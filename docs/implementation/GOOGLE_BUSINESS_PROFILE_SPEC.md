# Google Business Profile: check, collect, create

Status: spec for approval. No code yet.

Decided on 2026-11-23:
- The profile is the public **Google Business Profile**, the listing on Google Maps and in the search panel.
- Listings are created **through Google's Business Profile API**. The team applies to Google for API access.
- Uploaded photos are stored in an **S3-compatible bucket**.

## 1. Goal

For each brand, the agent:
1. Finds out whether the brand already has a Google Business Profile.
2. If it doesn't, asks the owner for the facts, text and photos it needs.
3. Prepares the complete listing as an approval item.
4. Once the owner approves, creates the listing through Google's API.
5. Tracks the listing until Google verifies it, then keeps checking it weekly.

The agent never invents a fact: name, address, phone, hours and category always come from the owner.
It may draft the description and suggest categories, which the owner edits or approves.

## 2. What Google requires (external, not code)

| # | Requirement | Who | Notes |
|---|---|---|---|
| G1 | Google Cloud project approved for the Business Profile APIs | Team | Application form; Google reviews it and the quota is 0 until approved. The approval can take weeks. |
| G2 | APIs enabled: My Business Account Management, Business Information, Verifications | Team | In the same Google Cloud project as the existing sign-in |
| G3 | OAuth consent screen gains the `business.manage` scope | Team | A sensitive scope, which may need Google's re-review of the consent screen |
| G4 | The brand owner signs in with the Google account that should own the listing | Owner | Through a new "Connect Google Business" button |
| G5 | **Verification** of every new listing (postcard, phone, SMS, email or video, as Google offers) | Owner | Required by Google; no tool can skip it. The agent shows the methods Google offers and records the outcome. |

Until G1 to G3 are done, everything in sections 3 to 5 works except the final create step, which says it's waiting for Google API access.

## 3. Detection: does the brand have a listing?

- Weekly, and when the owner asks, the agent runs a Google Places search through Serper (`https://google.serper.dev/places`) for the brand's name, plus the city or country when known.
- A place counts as the brand's listing when its website matches the brand's domain.
  It also counts when the name matches closely and the address matches the owner's address.
- Stored result: found or not found, the listing link (CID), name, category, address, rating and review count, plus when it was checked.
- When the owner has connected Google Business (G4), the API's own list of the owner's locations is used instead, because it is exact.
- Detection never creates anything.

## 4. Collection: what the agent asks for

Shown in a new **Google Business** tab in Agent mode and offered in chat ("Your brand has no Google Business Profile. Want to create one?").

| Field | Source | Rule |
|---|---|---|
| Business name | Owner | Must match the real-world name (Google policy); no keywords added |
| Primary category, up to 9 extra | Owner picks; the agent suggests from the site analysis | Google's category list |
| Address, or service area | Owner | A service-area business can hide its address |
| Phone | Owner | |
| Website | The brand's URL | Prefilled |
| Opening hours | Owner | |
| Description | The agent drafts it from the brand profile; the owner edits | At most 750 characters; no links or promotions (Google policy) |
| Photos: logo, cover, up to 10 more | Owner uploads | JPG or PNG, 10 KB to 4 MB (Google allows 5; Vercel's request limit is 4.5), at least 250 px on each side; larger photos are resized in the browser; stored in S3 |

- Photos go to a private S3 bucket under `gbp/<org-id>/`, and are shown back through short-lived signed links.
- Each upload is checked for type, size and dimensions before it's accepted.

## 5. Approval and creation

- The finished listing becomes a `create_business_profile` **action** with a version and a content hash, exactly like articles and emails.
- Every Phase 1 control applies:
  - individual approval of the exact version;
  - an edit makes a new version;
  - the append-only trail;
  - the safety-change block.
- On approval, dispatch calls Google:
  1. Create the location (`accounts.locations.create`) under the owner's account.
  2. Upload the photos (media API) from the S3 copies.
  3. Fetch the verification options Google offers and show them to the owner.
- Outcomes follow the existing send protocol:
  - **success** stores the location id and link;
  - **failure before Google accepted** is retryable or permanent, by error;
  - **timeout or unknown** goes to `needs_reconciliation`. Reconciliation lists the owner's locations and matches by name and address, never creating a second listing.
- After creation, the weekly check reads the listing's state through the API: unverified, pending verification, verified or suspended. The tab shows it.

## 6. Data model (one migration)

| Model | Purpose |
|---|---|
| `BusinessProfileCheck` | Each detection result (found, link, rating, reviews, raw place data), one row per check |
| `BusinessProfileDraft` | The owner's facts and the agent's suggestions, edited in the tab; becomes the action's payload |
| `BusinessProfilePhoto` | One uploaded photo: S3 key, kind (logo / cover / other), size, dimensions, checksum |
| Integration provider `google_business` | The owner's OAuth tokens (encrypted, like Gmail and Search Console) |

## 7. API (owner-only, signed like the other Agent endpoints)

| Method | Path | Purpose |
|---|---|---|
| GET | `programs/<slug>/business-profile/` | Latest check, draft, photos (signed links), action state, verification state |
| POST | `programs/<slug>/business-profile/check/` | Check now (rate-limited, once an hour) |
| PATCH | `programs/<slug>/business-profile/draft/` | Save the owner's fields |
| POST | `programs/<slug>/business-profile/photos/` | Upload one photo (multipart) |
| DELETE | `programs/<slug>/business-profile/photos/<id>/` | Remove a photo |
| POST | `programs/<slug>/business-profile/submit/` | Turn the draft into an approval action |
| GET | `oauth/google-business/start/`, `.../callback/` | Connect the owner's Google account (`business.manage`) |

## 8. Frontend

- **Google Business tab:** listing found (with link, rating, reviews) or "No listing found".
  When there's no listing, it shows the form, the photo uploader, a "Draft description" button and "Submit for approval".
- **Approvals:** the listing appears like any other draft, with every field and the photos.
- **Chat:** the agent can say whether a listing exists and point to the tab. The chat doesn't take uploads; photos go through the tab.

## 9. Configuration

| Setting | Purpose |
|---|---|
| `GBP_S3_BUCKET`, `GBP_S3_REGION`, `GBP_S3_ACCESS_KEY_ID`, `GBP_S3_SECRET_ACCESS_KEY`, optional `GBP_S3_ENDPOINT_URL` | Photo storage (S3 or compatible, such as R2) |
| `GOOGLE_BUSINESS_API_ENABLED` | Off until G1 to G3 are done; the create step then says it's waiting for Google |
| Existing `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Reused for the new scope |

## 10. Tests

- Detection: website match, name and address match, not found, Serper failure (unknown, never "not found").
- Upload validation: type, size, dimensions, and other brands' photos refused.
- Draft: required fields, description length limit.
- The action goes through approval, edit and version, and dispatch with a fake Google client covering success, retryable failure, unknown outcome and reconciliation (no duplicate).
- OAuth connect and disconnect, with encrypted tokens.
- Authorization: another brand's owner gets 404 on every endpoint.

## 11. Order of work

1. Detection and the read-only tab: visible first.
2. Draft form, photo upload to S3, description drafting.
3. Approval action, Google OAuth connect, API create behind `GOOGLE_BUSINESS_API_ENABLED`, reconciliation.
4. Verification state and the weekly re-check.

Steps 1 and 2 are useful before Google approves API access.
Step 3 can be fully tested with a fake Google client, then switched on when access arrives.

## 12. Open items

- The team's Google Cloud API access application (G1).
- An S3 bucket and credentials for photos.
- Whether agencies may create listings for client brands (Google allows agencies with the owner's access); this spec assumes owner-only, matching approvals.
