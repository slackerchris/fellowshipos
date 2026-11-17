# FellowshipNAS phone app requirements and design

Draft for review. This is a separate mobile project. The
[NAS release list](fellowship-nas-release.md) continues to guide NAS release work.

## What the app does

Back up photos and videos from a household phone to FellowshipNAS. Show whether
the backup worked and let the owner retrieve a copy. On Android, make NAS shares
available from the system file picker so other apps can open files.

Android is the proposed first platform because we have a specific design for
its file picker. Decide on iPhone scope after testing backup on a real phone.

## First version

| Requirement | What the user can do |
| --- | --- |
| Connect to a NAS | Enter the NAS address and sign in with a regular NAS account. The app shows which shares that account can access. |
| Choose what to back up | Allow access to photos and videos and see what the app can back up. Start with the accessible camera library. Add album selection if it solves a real need on the target phones. |
| Choose where it goes | Select a writable share and folder. Suggest a separate subfolder for each phone. |
| Run a backup | Tap **Back up now**. Default uploads to Wi-Fi. Copy the original files without changing their contents. |
| Understand the result | See what is queued, what failed, and when the last backup finished successfully. Retry after a network interruption or NAS restart. |
| Recover a copy | Browse backed-up photos and videos, open one, and save a copy to the phone. Deleting a photo from the phone leaves its NAS copy in place. |
| Open other NAS files on Android | Choose **Fellowship NAS** in the Android system picker, browse permitted shares, and open a file from an app that uses the picker. Use the NAS web Files app for file management. |

## Design

**App screens.** Setup collects the NAS address, account, source and destination.
Backup shows progress, recent results and **Back up now**. The backup folder view
offers preview and export using system viewers where practical. Settings lets the
user change the destination, network preference or connection.

**NAS connection.** Prototype against the existing HTTPS WebDAV endpoint at
`/dav/`. Use the signed-in user's existing share permissions for listing, upload
and download. Store credentials using the phone's protected credential storage
and verify the NAS certificate. Before committing to WebDAV as the app interface,
test normal-user sign-in, home LAN certificate setup, large videos, and safe
completion of uploads. Write down any concrete server gap the prototype finds.

**Backup queue.** Persist queue state and completed items on the phone so an app
restart can resume the job. Track each item against its NAS and destination. Plan
to upload to a temporary file and publish it under its final name only after a
successful transfer, subject to proving the existing WebDAV behavior. Check that
retrying after a lost response leaves one completed copy. Keep two different
photos with the same filename and preserve existing files at the destination.
Start with foreground uploads and restarting interrupted files. Automatic
background backup is a follow-up milestone that needs real-device testing.

**Android file picker.** Implement a read-only `DocumentsProvider` under
Android's Storage Access Framework. Show a root for the configured NAS, list
permitted shares and folders, and fetch a file when Android asks to open it.
Handle a NAS that's offline or an account that needs to sign in again. Keep a
copy or stream only as needed to open the selected file.
[Android document provider guide](https://developer.android.com/guide/topics/providers/create-document-provider)

## Build order and finish lines

1. **Connection prototype.** On a real Android phone, connect to a test NAS as
   a regular user. List shares, upload a photo and a video to a writable folder,
   and read them back. A read-only share refuses uploads.
2. **Backup and recovery.** Interrupt an upload, close and reopen the app, then
   retry. Check that the NAS has one complete copy with matching contents.
   Back up two items with the same name. Delete a backed-up photo from the
   phone, then save a copy back from the NAS.
3. **Android picker.** Use another app's Open action to find and open a NAS
   file. Check that private shares for a different account stay hidden and
   that the user gets a clear result if the NAS is offline.
4. **Automatic backup.** Test Wi-Fi-only behavior and recovery after the phone
   is offline. Document when the phone actually runs background uploads.

## Decisions to revisit after the prototype

- Confirm Android-first and decide when an iPhone version makes sense.
- Try connecting on the home LAN before deciding how to support access away
  from home.
- Revisit saving to NAS from other apps, offline copies, and picker write
  support after using the read-only picker. Keep the web Files app available for
  broader browsing and management.
