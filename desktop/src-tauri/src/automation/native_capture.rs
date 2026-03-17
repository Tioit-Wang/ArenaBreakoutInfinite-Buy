use std::sync::{Arc, Mutex, Once};

use anyhow::{Context, Result, anyhow};
use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    BeginPaint, CreatePen, CreateSolidBrush, DeleteObject, DrawTextW, EndPaint, FillRect,
    FrameRect, GetStockObject, HGDIOBJ, InvalidateRect, PAINTSTRUCT, PS_DASH, PS_SOLID,
    Rectangle, SelectObject, SetBkMode, SetTextColor, TRANSPARENT, UpdateWindow,
    DRAW_TEXT_FORMAT, NULL_BRUSH,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Input::KeyboardAndMouse::{ReleaseCapture, SetCapture, SetFocus, VK_ESCAPE};
use windows::Win32::UI::WindowsAndMessaging::{
    CREATESTRUCTW, CS_HREDRAW, CS_VREDRAW, CreateWindowExW, DefWindowProcW, DestroyWindow,
    DispatchMessageW, GWLP_USERDATA, GetClientRect, GetMessageW, GetSystemMetrics,
    GetWindowLongPtrW, HMENU, IDC_CROSS, LWA_ALPHA, LoadCursorW, MSG, PostQuitMessage,
    RegisterClassW, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN,
    SM_YVIRTUALSCREEN, SW_SHOW, SetCursor, SetForegroundWindow, SetLayeredWindowAttributes,
    SetWindowLongPtrW, ShowWindow, TranslateMessage, WM_DESTROY, WM_ERASEBKGND, WM_KEYDOWN,
    WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, WM_NCCREATE, WM_NCDESTROY, WM_PAINT,
    WM_RBUTTONDOWN, WM_SETCURSOR, WNDCLASSW, WS_EX_LAYERED, WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST, WS_POPUP,
};
use windows::core::{PCWSTR, w};

use crate::automation::capture::CaptureRegion;
use crate::automation::common::{CARD_HEIGHT, CARD_WIDTH};

const MIN_SELECTION: i32 = 4;
const INSTRUCTION_BUFFER: i32 = 56;

#[derive(Debug, Clone, Copy)]
pub enum NativeCaptureMode {
    Template,
    GoodsCard,
}

#[derive(Debug, Clone, Copy)]
pub struct NativeCaptureOptions {
    pub mode: NativeCaptureMode,
    pub card_width: i32,
    pub card_height: i32,
}

impl NativeCaptureOptions {
    pub fn template() -> Self {
        Self {
            mode: NativeCaptureMode::Template,
            card_width: CARD_WIDTH,
            card_height: CARD_HEIGHT,
        }
    }

    pub fn goods_card() -> Self {
        Self {
            mode: NativeCaptureMode::GoodsCard,
            card_width: CARD_WIDTH,
            card_height: CARD_HEIGHT,
        }
    }
}

#[derive(Debug)]
struct SessionState {
    options: NativeCaptureOptions,
    virtual_x: i32,
    virtual_y: i32,
    width: i32,
    height: i32,
    start: Option<(i32, i32)>,
    cursor: (i32, i32),
    dragging: bool,
    result: Option<Option<CaptureRegion>>,
}

impl SessionState {
    fn new(options: NativeCaptureOptions) -> Result<Self> {
        let virtual_x = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) };
        let virtual_y = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) };
        let width = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) };
        let height = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) };
        if width <= 0 || height <= 0 {
            return Err(anyhow!("failed to resolve virtual screen bounds"));
        }
        Ok(Self {
            options,
            virtual_x,
            virtual_y,
            width,
            height,
            start: None,
            cursor: (0, 0),
            dragging: false,
            result: None,
        })
    }

    fn fixed_card_size(&self) -> (i32, i32) {
        // 保持与 Python/Tk 版一致，物品卡片选择框使用固定像素尺寸。
        (self.options.card_width, self.options.card_height)
    }

    fn template_rect(&self) -> Option<RECT> {
        let (sx, sy) = self.start?;
        let (cx, cy) = self.cursor;
        Some(RECT {
            left: sx.min(cx),
            top: sy.min(cy),
            right: sx.max(cx),
            bottom: sy.max(cy),
        })
    }

    fn fixed_rect(&self) -> RECT {
        let (cursor_x, cursor_y) = self.cursor;
        let (width, height) = self.fixed_card_size();
        RECT {
            left: cursor_x - (width / 2),
            top: cursor_y - (height / 2),
            right: cursor_x - (width / 2) + width,
            bottom: cursor_y - (height / 2) + height,
        }
    }

    fn active_rect(&self) -> Option<RECT> {
        match self.options.mode {
            NativeCaptureMode::Template => self.template_rect(),
            NativeCaptureMode::GoodsCard => Some(self.fixed_rect())
                .filter(|rect| rect.right > rect.left && rect.bottom > rect.top),
        }
    }

    fn finish(&mut self, rect: Option<RECT>) {
        self.result = Some(rect.and_then(|rect| self.to_screen_region(rect)));
    }

    fn to_screen_region(&self, rect: RECT) -> Option<CaptureRegion> {
        let width = rect.right - rect.left;
        let height = rect.bottom - rect.top;
        if width < MIN_SELECTION || height < MIN_SELECTION {
            return None;
        }
        Some(CaptureRegion {
            x: self.virtual_x + rect.left,
            y: self.virtual_y + rect.top,
            width,
            height,
        })
    }
}

pub fn select_region(options: NativeCaptureOptions) -> Result<Option<CaptureRegion>> {
    register_window_class()?;
    let state = Arc::new(Mutex::new(SessionState::new(options)?));
    let state_ptr = Arc::into_raw(state.clone());
    let hinstance = unsafe { GetModuleHandleW(None) }.context("failed to resolve module handle")?;
    let bounds = {
        let guard = state.lock().expect("capture state mutex poisoned");
        (guard.virtual_x, guard.virtual_y, guard.width, guard.height)
    };

    let hwnd = unsafe {
        CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
            CAPTURE_CLASS_NAME,
            w!("ArenaBuyer Native Capture"),
            WS_POPUP,
            bounds.0,
            bounds.1,
            bounds.2,
            bounds.3,
            Some(HWND::default()),
            Some(HMENU::default()),
            Some(hinstance.into()),
            Some(state_ptr.cast_mut().cast()),
        )
    }
    .context("failed to create native capture window")?;

    unsafe {
        SetLayeredWindowAttributes(hwnd, COLORREF(0), 140, LWA_ALPHA)
            .context("failed to configure layered capture window")?;
        let _ = ShowWindow(hwnd, SW_SHOW);
        if !UpdateWindow(hwnd).as_bool() {
            return Err(anyhow!("failed to update capture window"));
        }
        let _ = SetForegroundWindow(hwnd);
        let _ = SetFocus(Some(hwnd));
    }

    let mut message = MSG::default();
    while unsafe { GetMessageW(&mut message, None, 0, 0) }.into() {
        unsafe {
            let _ = TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }

    let result = state
        .lock()
        .expect("capture state mutex poisoned")
        .result
        .clone();
    Ok(result.ok_or_else(|| anyhow!("native capture session ended without a result"))?)
}

static REGISTER_CAPTURE_CLASS: Once = Once::new();
const CAPTURE_CLASS_NAME: PCWSTR = w!("ArenaBuyerNativeCaptureSelector");

fn register_window_class() -> Result<()> {
    let mut result = Ok(());
    REGISTER_CAPTURE_CLASS.call_once(|| {
        let hinstance = match unsafe { GetModuleHandleW(None) } {
            Ok(value) => value,
            Err(error) => {
                result = Err(error.into());
                return;
            }
        };
        let wc = WNDCLASSW {
            style: CS_HREDRAW | CS_VREDRAW,
            lpfnWndProc: Some(capture_window_proc),
            hInstance: hinstance.into(),
            lpszClassName: CAPTURE_CLASS_NAME,
            hCursor: unsafe { LoadCursorW(None, IDC_CROSS) }.unwrap_or_default(),
            ..Default::default()
        };
        let atom = unsafe { RegisterClassW(&wc) };
        if atom == 0 {
            result = Err(anyhow!("failed to register native capture window class"));
        }
    });
    result
}

unsafe extern "system" fn capture_window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match message {
        WM_NCCREATE => {
            let create = &*(lparam.0 as *const CREATESTRUCTW);
            let ptr = create.lpCreateParams as *const Mutex<SessionState>;
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, ptr as isize);
            return LRESULT(1);
        }
        WM_SETCURSOR => {
            let _ = SetCursor(Some(LoadCursorW(None, IDC_CROSS).unwrap_or_default()));
            return LRESULT(1);
        }
        WM_ERASEBKGND => return LRESULT(1),
        WM_MOUSEMOVE => {
            if let Some(state) = state_from_hwnd(hwnd) {
                let mut guard = state.lock().expect("capture state mutex poisoned");
                guard.cursor = point_from_lparam(lparam);
                let _ = InvalidateRect(Some(hwnd), None, false);
            }
            return LRESULT(0);
        }
        WM_LBUTTONDOWN => {
            if let Some(state) = state_from_hwnd(hwnd) {
                let mut guard = state.lock().expect("capture state mutex poisoned");
                guard.cursor = point_from_lparam(lparam);
                match guard.options.mode {
                    NativeCaptureMode::Template => {
                        guard.start = Some(guard.cursor);
                        guard.dragging = true;
                        let _ = SetCapture(hwnd);
                    }
                    NativeCaptureMode::GoodsCard => {
                        let rect = guard.fixed_rect();
                        guard.finish(Some(rect));
                        drop(guard);
                        let _ = DestroyWindow(hwnd);
                    }
                }
                let _ = InvalidateRect(Some(hwnd), None, false);
            }
            return LRESULT(0);
        }
        WM_LBUTTONUP => {
            if let Some(state) = state_from_hwnd(hwnd) {
                let mut guard = state.lock().expect("capture state mutex poisoned");
                guard.cursor = point_from_lparam(lparam);
                if guard.dragging {
                    guard.dragging = false;
                    let _ = ReleaseCapture();
                    let rect = guard.template_rect();
                    if rect
                        .map(|rect| (rect.right - rect.left) >= MIN_SELECTION && (rect.bottom - rect.top) >= MIN_SELECTION)
                        .unwrap_or(false)
                    {
                        guard.finish(rect);
                        drop(guard);
                        let _ = DestroyWindow(hwnd);
                    } else {
                        guard.start = None;
                        let _ = InvalidateRect(Some(hwnd), None, false);
                    }
                }
            }
            return LRESULT(0);
        }
        WM_RBUTTONDOWN => {
            if let Some(state) = state_from_hwnd(hwnd) {
                let mut guard = state.lock().expect("capture state mutex poisoned");
                guard.finish(None);
            }
            let _ = DestroyWindow(hwnd);
            return LRESULT(0);
        }
        WM_KEYDOWN => {
            if wparam.0 as u16 == VK_ESCAPE.0 {
                if let Some(state) = state_from_hwnd(hwnd) {
                    let mut guard = state.lock().expect("capture state mutex poisoned");
                    guard.finish(None);
                }
                let _ = DestroyWindow(hwnd);
                return LRESULT(0);
            }
        }
        WM_PAINT => {
            paint_capture_window(hwnd);
            return LRESULT(0);
        }
        WM_DESTROY => {
            PostQuitMessage(0);
            return LRESULT(0);
        }
        WM_NCDESTROY => {
            let ptr = SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
            if ptr != 0 {
                drop(Arc::from_raw(ptr as *const Mutex<SessionState>));
            }
        }
        _ => {}
    }
    DefWindowProcW(hwnd, message, wparam, lparam)
}

unsafe fn paint_capture_window(hwnd: HWND) {
    let mut paint = PAINTSTRUCT::default();
    let dc = BeginPaint(hwnd, &mut paint);
    let mut client = RECT::default();
    let _ = GetClientRect(hwnd, &mut client);

    let overlay_brush = CreateSolidBrush(COLORREF(0x101010));
    let _ = FillRect(dc, &client, overlay_brush);
    let _ = DeleteObject(HGDIOBJ(overlay_brush.0));

    if let Some(state) = state_from_hwnd(hwnd) {
        let guard = state.lock().expect("capture state mutex poisoned");
        let instruction = match guard.options.mode {
            NativeCaptureMode::Template => "拖拽框选区域，Esc / 右键取消",
            NativeCaptureMode::GoodsCard => "移动定位卡片，左键确认，Esc / 右键取消",
        };
        let mut instruction_rect = RECT {
            left: 0,
            top: 12,
            right: guard.width,
            bottom: INSTRUCTION_BUFFER,
        };
        let _ = SetBkMode(dc, TRANSPARENT);
        let _ = SetTextColor(dc, COLORREF(0x00FFFFFF));
        let mut text: Vec<u16> = instruction.encode_utf16().chain([0]).collect();
        let _ = DrawTextW(
            dc,
            &mut text,
            &mut instruction_rect,
            DRAW_TEXT_FORMAT(0x00000001 | 0x00000004 | 0x00000020),
        );

        if let Some(rect) = guard.active_rect() {
            match guard.options.mode {
                NativeCaptureMode::Template => {
                    let border_pen = CreatePen(PS_SOLID, 2, COLORREF(0x00FFFFFF));
                    let border_brush = CreateSolidBrush(COLORREF(0x00242424));
                    let old_pen = SelectObject(dc, HGDIOBJ(border_pen.0));
                    let old_brush = SelectObject(dc, HGDIOBJ(border_brush.0));
                    let _ = Rectangle(dc, rect.left, rect.top, rect.right, rect.bottom);
                    let _ = SelectObject(dc, old_pen);
                    let _ = SelectObject(dc, old_brush);
                    let _ = DeleteObject(HGDIOBJ(border_pen.0));
                    let _ = DeleteObject(HGDIOBJ(border_brush.0));
                }
                NativeCaptureMode::GoodsCard => {
                    let (top_rect, middle_rect, bottom_rect) = fixed_card_sections(rect);
                    fill_rect(dc, top_rect, COLORREF(0x00FF7C2D));
                    fill_rect(dc, middle_rect, COLORREF(0x004DD8FF));
                    fill_rect(dc, bottom_rect, COLORREF(0x0043A02E));

                    let outline_brush = CreateSolidBrush(COLORREF(0x00CCCCCC));
                    let _ = FrameRect(dc, &rect, outline_brush);
                    let _ = DeleteObject(HGDIOBJ(outline_brush.0));

                    let inner = fixed_inner_rect(rect);
                    let inner_pen = CreatePen(PS_DASH, 1, COLORREF(0x00333333));
                    let old_pen = SelectObject(dc, HGDIOBJ(inner_pen.0));
                    let old_brush = SelectObject(dc, GetStockObject(NULL_BRUSH));
                    let _ = Rectangle(dc, inner.left, inner.top, inner.right, inner.bottom);
                    let _ = SelectObject(dc, old_pen);
                    let _ = SelectObject(dc, old_brush);
                    let _ = DeleteObject(HGDIOBJ(inner_pen.0));
                }
            }
        }
    }

    let _ = EndPaint(hwnd, &paint);
}

unsafe fn state_from_hwnd(hwnd: HWND) -> Option<Arc<Mutex<SessionState>>> {
    let ptr = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) };
    if ptr == 0 {
        return None;
    }
    let state = unsafe { Arc::from_raw(ptr as *const Mutex<SessionState>) };
    let clone = state.clone();
    let _ = unsafe { Arc::into_raw(state) };
    Some(clone)
}

fn point_from_lparam(lparam: LPARAM) -> (i32, i32) {
    let x = (lparam.0 & 0xffff) as i16 as i32;
    let y = ((lparam.0 >> 16) & 0xffff) as i16 as i32;
    (x, y)
}

fn fixed_inner_rect(rect: RECT) -> RECT {
    let width = rect.right - rect.left;
    let height = rect.bottom - rect.top;
    let inner_left =
        ((width as f64) * (crate::automation::common::CARD_MARGIN_LR as f64 / CARD_WIDTH as f64))
            .round() as i32;
    let inner_top = ((height as f64)
        * ((crate::automation::common::CARD_TOP_HEIGHT + crate::automation::common::CARD_MARGIN_TB) as f64
            / CARD_HEIGHT as f64))
        .round() as i32;
    let inner_width = ((width as f64)
        * (crate::automation::common::CARD_INNER_WIDTH as f64 / CARD_WIDTH as f64))
        .round() as i32;
    let inner_height = ((height as f64)
        * (crate::automation::common::CARD_INNER_HEIGHT as f64 / CARD_HEIGHT as f64))
        .round() as i32;
    RECT {
        left: rect.left + inner_left,
        top: rect.top + inner_top,
        right: rect.left + inner_left + inner_width,
        bottom: rect.top + inner_top + inner_height,
    }
}

fn fixed_card_sections(rect: RECT) -> (RECT, RECT, RECT) {
    let height = (rect.bottom - rect.top).max(1);
    let top_height = ((height as f64)
        * (crate::automation::common::CARD_TOP_HEIGHT as f64 / CARD_HEIGHT as f64))
        .round() as i32;
    let bottom_height = ((height as f64)
        * (crate::automation::common::CARD_BOTTOM_HEIGHT as f64 / CARD_HEIGHT as f64))
        .round() as i32;
    let middle_top = rect.top + top_height.clamp(1, height);
    let middle_bottom = (rect.bottom - bottom_height.clamp(1, height)).max(middle_top);
    (
        RECT {
            left: rect.left,
            top: rect.top,
            right: rect.right,
            bottom: middle_top,
        },
        RECT {
            left: rect.left,
            top: middle_top,
            right: rect.right,
            bottom: middle_bottom,
        },
        RECT {
            left: rect.left,
            top: middle_bottom,
            right: rect.right,
            bottom: rect.bottom,
        },
    )
}

unsafe fn fill_rect(
    dc: windows::Win32::Graphics::Gdi::HDC,
    rect: RECT,
    color: COLORREF,
) {
    let brush = CreateSolidBrush(color);
    let _ = FillRect(dc, &rect, brush);
    let _ = DeleteObject(HGDIOBJ(brush.0));
}

#[cfg(test)]
mod tests {
    use windows::Win32::Foundation::RECT;

    use super::fixed_inner_rect;

    #[test]
    fn computes_goods_inner_rect_for_base_card_size() {
        let rect = RECT {
            left: 0,
            top: 0,
            right: 165,
            bottom: 212,
        };
        let inner = fixed_inner_rect(rect);
        assert_eq!(inner.left, 30);
        assert_eq!(inner.top, 40);
        assert_eq!(inner.right - inner.left, 105);
        assert_eq!(inner.bottom - inner.top, 122);
    }

    #[test]
    fn computes_goods_inner_rect_for_scaled_card_size() {
        let rect = RECT {
            left: 0,
            top: 0,
            right: 330,
            bottom: 424,
        };
        let inner = fixed_inner_rect(rect);
        assert_eq!(inner.left, 60);
        assert_eq!(inner.top, 80);
        assert_eq!(inner.right - inner.left, 210);
        assert_eq!(inner.bottom - inner.top, 244);
    }
}
