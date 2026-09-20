# **NerazimNet - Release Notes (v1.1.1)**

This release focuses on massive user-experience and reliability improvements, from a simpler setup process to more stable, everyday tunnel connections.

## **🚀 Features & Documentation**

* **New "Getting Started" Guide:** The project README.md has been completely rewritten! It now details the modern, simplified workflow, guiding new users through the one-click automated server provisioning process. This makes setup faster and more intuitive than ever.

## **🐛 Fixes & Reliability**

* **Fixed the "Localhost Lottery" Bug!** 🌐 Tunnels are now significantly more reliable. We now force the SSH client to use IPv4 (via the -4 flag), which solves a frustrating issue where a tunnel would fail on one PC (resolving localhost to [::1]) but work perfectly on another (resolving to 127.0.0.1).

## **⚙️ Housekeeping & Build Process**

* **Smarter Release Workflow:** The GitHub Actions release process (build-and-release.yml) has been updated. It's now synced with all our latest build improvements and is configured to use this changelog for the release notes.