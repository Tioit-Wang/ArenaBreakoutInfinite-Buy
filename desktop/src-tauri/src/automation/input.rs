use anyhow::{Result, anyhow};

#[cfg(target_os = "windows")]
use std::{mem::size_of, thread, time::Duration};

#[cfg(target_os = "windows")]
use windows::Win32::UI::{
    Input::KeyboardAndMouse::{
        INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBD_EVENT_FLAGS, KEYBDINPUT,
        KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, MOUSE_EVENT_FLAGS, MOUSEEVENTF_LEFTDOWN,
        MOUSEEVENTF_LEFTUP, MOUSEINPUT, SendInput, VIRTUAL_KEY, VK_BACK, VK_CONTROL,
    },
    WindowsAndMessaging::SetCursorPos,
};

#[cfg(target_os = "windows")]
fn send(inputs: &[INPUT]) -> Result<()> {
    if inputs.is_empty() {
        return Ok(());
    }
    let written = unsafe { SendInput(inputs, size_of::<INPUT>() as i32) };
    if written != inputs.len() as u32 {
        return Err(anyhow!(
            "SendInput only wrote {written}/{} events",
            inputs.len()
        ));
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn key_input(vk: VIRTUAL_KEY, flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: vk,
                wScan: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

#[cfg(target_os = "windows")]
fn unicode_input(code_unit: u16, keyup: bool) -> INPUT {
    let mut flags = KEYEVENTF_UNICODE;
    if keyup {
        flags |= KEYEVENTF_KEYUP;
    }
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(0),
                wScan: code_unit,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

#[cfg(target_os = "windows")]
fn left_mouse_input(flags: MOUSE_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx: 0,
                dy: 0,
                mouseData: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

#[cfg(target_os = "windows")]
fn ctrl_a_clear() -> Result<()> {
    let inputs = [
        key_input(VK_CONTROL, KEYBD_EVENT_FLAGS(0)),
        key_input(VIRTUAL_KEY(0x41), KEYBD_EVENT_FLAGS(0)),
        key_input(VIRTUAL_KEY(0x41), KEYEVENTF_KEYUP),
        key_input(VK_CONTROL, KEYEVENTF_KEYUP),
        key_input(VK_BACK, KEYBD_EVENT_FLAGS(0)),
        key_input(VK_BACK, KEYEVENTF_KEYUP),
    ];
    send(&inputs)?;
    thread::sleep(Duration::from_millis(25));
    Ok(())
}

#[cfg(target_os = "windows")]
pub fn click_point(x: i32, y: i32) -> Result<()> {
    unsafe { SetCursorPos(x, y) }
        .map_err(|error| anyhow!("SetCursorPos({x}, {y}) failed: {error}"))?;
    thread::sleep(Duration::from_millis(18));
    let inputs = [
        left_mouse_input(MOUSEEVENTF_LEFTDOWN),
        left_mouse_input(MOUSEEVENTF_LEFTUP),
    ];
    send(&inputs)?;
    thread::sleep(Duration::from_millis(15));
    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub fn click_point(_x: i32, _y: i32) -> Result<()> {
    bail!("native input is only implemented on Windows")
}

#[cfg(target_os = "windows")]
pub fn type_text(value: &str) -> Result<()> {
    ctrl_a_clear()?;
    let mut inputs = Vec::with_capacity(value.encode_utf16().count() * 2 + 2);
    for code_unit in value.encode_utf16() {
        inputs.push(unicode_input(code_unit, false));
        inputs.push(unicode_input(code_unit, true));
    }
    send(&inputs)?;
    thread::sleep(Duration::from_millis(20));
    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub fn type_text(_value: &str) -> Result<()> {
    bail!("native input is only implemented on Windows")
}
