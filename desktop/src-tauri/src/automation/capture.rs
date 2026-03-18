use std::path::Path;

use anyhow::{Context, Result, bail};
use image::RgbaImage;
use serde::{Deserialize, Serialize};

#[cfg(target_os = "windows")]
use windows::Win32::Graphics::Gdi::{
    BI_RGB, BITMAPINFO, BITMAPINFOHEADER, BitBlt, CAPTUREBLT, CreateCompatibleBitmap,
    CreateCompatibleDC, DIB_RGB_COLORS, DeleteDC, DeleteObject, GetDC, GetDIBits, HGDIOBJ,
    ReleaseDC, ROP_CODE, SRCCOPY, SelectObject,
};
#[cfg(target_os = "windows")]
use windows::Win32::UI::WindowsAndMessaging::{
    GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN,
    SM_YVIRTUALSCREEN,
};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptureRegion {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}

#[derive(Debug, Clone, Copy)]
struct NormalizedRegion {
    x: i32,
    y: i32,
    width: i32,
    height: i32,
}

#[derive(Debug, Clone)]
pub struct CapturedImage {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub image: RgbaImage,
}

pub fn save_region_png(region: &CaptureRegion, output_path: &Path) -> Result<()> {
    let region = normalize_region(region)?;

    if let Some(parent) = output_path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }

    #[cfg(target_os = "windows")]
    {
        let image = capture_region_windows_gdi(region)?;
        image
            .save(output_path)
            .with_context(|| format!("failed to save capture to {}", output_path.display()))?;
    }

    #[cfg(not(target_os = "windows"))]
    {
        bail!("screen capture is only implemented on Windows")
    }

    Ok(())
}

pub fn capture_full_screen() -> Result<CapturedImage> {
    #[cfg(target_os = "windows")]
    {
        let region = virtual_screen_region()?;
        let image = capture_region_windows_gdi(region)?;
        return Ok(CapturedImage {
            x: region.x,
            y: region.y,
            width: region.width,
            height: region.height,
            image,
        });
    }

    #[cfg(not(target_os = "windows"))]
    {
        bail!("full screen capture is only implemented on Windows")
    }
}

fn normalize_region(region: &CaptureRegion) -> Result<NormalizedRegion> {
    if region.width <= 0 || region.height <= 0 {
        bail!("capture region must be positive")
    }

    #[cfg(target_os = "windows")]
    {
        let virtual_x = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) };
        let virtual_y = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) };
        let virtual_width = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) };
        let virtual_height = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) };

        if virtual_width <= 0 || virtual_height <= 0 {
            bail!("failed to resolve virtual screen bounds")
        }

        let max_x = virtual_x.saturating_add(virtual_width);
        let max_y = virtual_y.saturating_add(virtual_height);
        let clipped_x1 = region.x.clamp(virtual_x, max_x.saturating_sub(1));
        let clipped_y1 = region.y.clamp(virtual_y, max_y.saturating_sub(1));
        let clipped_x2 = region.x.saturating_add(region.width).clamp(clipped_x1 + 1, max_x);
        let clipped_y2 = region.y.saturating_add(region.height).clamp(clipped_y1 + 1, max_y);

        return Ok(NormalizedRegion {
            x: clipped_x1,
            y: clipped_y1,
            width: clipped_x2 - clipped_x1,
            height: clipped_y2 - clipped_y1,
        });
    }

    #[cfg(not(target_os = "windows"))]
    {
        bail!("screen capture is only implemented on Windows")
    }
}

#[cfg(target_os = "windows")]
fn virtual_screen_region() -> Result<NormalizedRegion> {
    let virtual_x = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) };
    let virtual_y = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) };
    let virtual_width = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) };
    let virtual_height = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) };

    if virtual_width <= 0 || virtual_height <= 0 {
        bail!("failed to resolve virtual screen bounds")
    }

    Ok(NormalizedRegion {
        x: virtual_x,
        y: virtual_y,
        width: virtual_width,
        height: virtual_height,
    })
}

#[cfg(target_os = "windows")]
fn capture_region_windows_gdi(region: NormalizedRegion) -> Result<RgbaImage> {
    // Windows 下改用 GDI BitBlt 抓取区域，行为更贴近 Python 版 pyautogui/Pillow。
    unsafe {
        let screen_dc = GetDC(None);
        if screen_dc.is_invalid() {
            bail!("failed to acquire the desktop DC")
        }

        let memory_dc = CreateCompatibleDC(Some(screen_dc));
        if memory_dc.is_invalid() {
            let _ = ReleaseDC(None, screen_dc);
            bail!("failed to create compatible memory DC")
        }

        let bitmap = CreateCompatibleBitmap(screen_dc, region.width, region.height);
        if bitmap.is_invalid() {
            let _ = DeleteDC(memory_dc);
            let _ = ReleaseDC(None, screen_dc);
            bail!("failed to create compatible bitmap")
        }

        let old_bitmap = SelectObject(memory_dc, HGDIOBJ(bitmap.0));
        if old_bitmap.is_invalid() {
            let _ = DeleteObject(HGDIOBJ(bitmap.0));
            let _ = DeleteDC(memory_dc);
            let _ = ReleaseDC(None, screen_dc);
            bail!("failed to select bitmap into memory DC")
        }

        let capture_result = (|| -> Result<RgbaImage> {
            BitBlt(
                memory_dc,
                0,
                0,
                region.width,
                region.height,
                Some(screen_dc),
                region.x,
                region.y,
                ROP_CODE(SRCCOPY.0 | CAPTUREBLT.0),
            )
            .context("failed to capture screen region with BitBlt")?;

            let mut bitmap_info = BITMAPINFO::default();
            bitmap_info.bmiHeader = BITMAPINFOHEADER {
                biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: region.width,
                biHeight: -region.height,
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            };

            let mut pixels = vec![0_u8; (region.width * region.height * 4) as usize];
            let copied_lines = GetDIBits(
                memory_dc,
                bitmap,
                0,
                region.height as u32,
                Some(pixels.as_mut_ptr().cast()),
                &mut bitmap_info,
                DIB_RGB_COLORS,
            );
            if copied_lines != region.height {
                bail!("GetDIBits copied {} scan lines, expected {}", copied_lines, region.height)
            }

            for pixel in pixels.chunks_exact_mut(4) {
                pixel.swap(0, 2);
                pixel[3] = 255;
            }

            RgbaImage::from_raw(region.width as u32, region.height as u32, pixels)
                .context("failed to build image buffer from GDI pixels")
        })();

        let _ = SelectObject(memory_dc, old_bitmap);
        let _ = DeleteObject(HGDIOBJ(bitmap.0));
        let _ = DeleteDC(memory_dc);
        let _ = ReleaseDC(None, screen_dc);

        capture_result
    }
}
