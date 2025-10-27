# WhatsApp Flow Setup Instructions

## Flow JSON Configuration

### Step 1: Update Your Domain

Open `onboarding_flow.json` and replace `your-domain.com` with your actual domain:

```json
"endpoint": "https://your-domain.com/webhook/flow"
```

### Step 2: Update Account Options (Optional)

If you want different banks, edit the `data-source` in the last screen:

```json
"data-source": [
  {
    "id": "0_Bank_1",
    "title": "Access Bank"
  },
  {
    "id": "1_Bank_2",
    "title": "GTBank"
  },
  {
    "id": "2_Bank_3",
    "title": "Zenith Bank"
  }
]
```

### Step 3: Upload to WhatsApp Manager

1. Go to [Meta Business Suite](https://business.facebook.com/)
2. Navigate to: **WhatsApp Manager** → **Flows**
3. Click **"Create Flow"** or **"Edit"** your existing flow
4. Paste the contents of `onboarding_flow.json` into the Flow Builder
5. Click **"Save"**

### Step 4: Configure Endpoint

1. In the Flow settings, set:

   - **Endpoint URL**: `https://your-domain.com/webhook/flow`
   - **Data API Version**: `3.0`

2. Ensure your endpoint is configured to:
   - Accept HTTPS connections
   - Handle encrypted data (we've already implemented decryption)
   - Return JSON responses

### Step 5: Test the Flow

1. Send a message to trigger your flow
2. Test each screen:
   - **Screen 1**: Enter an 11-digit BVN (e.g., `12345678901`)
   - **Screen 2**: Enter a 6-digit OTP (e.g., `123456`)
   - **Screen 3**: Select one or more accounts
3. Click **"Submit"** to complete

## Flow Behavior

### Screen 1: BVN Entry (RECOMMEND)

- User enters 11-digit BVN
- Webhook validates BVN format
- If valid → proceed to next screen
- If invalid → show error message

### Screen 2: OTP Entry (RATE)

- User enters 6-digit OTP
- Webhook validates OTP format
- If valid → proceed to next screen
- If invalid → show error message

### Screen 3: Account Selection (screen_usqvdd)

- User selects one or more accounts
- On completion, webhook receives all data:
  - BVN
  - OTP
  - Selected accounts

## Webhook Integration

Your webhook at `/webhook/flow` handles:

1. **BVN Validation**: Checks 11-digit format
2. **OTP Validation**: Checks 6-digit format
3. **Flow Completion**: Receives all final data

### Response Format

On success, navigate to next screen:

```json
{
  "actions": [
    {
      "action": "navigate",
      "next": {
        "name": "RATE",
        "parameters": {
          "screen_0_BVN_0": "BVN_VALUE"
        }
      }
    }
  ]
}
```

On error, show field error:

```json
{
  "errors": [
    {
      "field": "BVN_c98209",
      "message": "Invalid BVN. Please check and enter a valid 11-digit BVN."
    }
  ]
}
```

## Files

- `onboarding_flow.json` - WhatsApp Flow configuration
- `apps/gateway/api/flow_webhook.py` - Webhook handler
- `shared/utils/flow_decryption.py` - Decryption logic
- `whatsapp_flow_private_key.pem` - Private key (keep secure!)
- `whatsapp_flow_public_key.pem` - Public key (uploaded to Meta)

## Security Notes

✅ **Encryption Enabled**: All data is encrypted with AES-GCM
✅ **Private Key Secure**: Stored in separate `.pem` file (not committed)
✅ **HTTPS Required**: Endpoint must use valid SSL certificate

## Troubleshooting

### Flow doesn't navigate

- Check webhook logs for errors
- Verify endpoint is accessible
- Check BVN/OTP validation logic

### Decryption errors

- Verify private key matches uploaded public key
- Check `WHATSAPP_FLOW_PRIVATE_KEY_PATH` environment variable
- Ensure private key file is accessible

### 421 errors

- Incorrect key pair uploaded to Meta
- Re-upload public key
- Restart gateway service
