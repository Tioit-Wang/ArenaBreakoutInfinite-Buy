The problem is that I already have a working program that sends a click to an inactive window that is not on TOPLEVEL, but it does not work with all windows. And I cannot understand what is the reason for this behavior. When I filter messages with spy ++, they are exactly the same as with a real mouse click, but in the end the game does not respond.

Initially, I coded it in python, but in the end it didn't work out for me and I decided to try C ++, despite the fact that I have no experience. Here's what I managed to put together in C ++.

#include <stdio.h>
#include <cstdlib>
#include <windows.h>
#include <winuser.h>
#include <conio.h>

LPCTSTR WindowName = L"Raid: Shadow Legends";
HWND hMU = FindWindow(NULL, WindowName);
int main() {
if (hMU)
{
int x = 16; //selected values ​​based on the readings of spy ++
int y = 266; //
WINDOWPOS wp = { hMU, NULL, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE };

        SendMessage(hMU, WM_SETCURSOR, (WPARAM)hMU, MAKELPARAM(HTCLIENT, WM_MOUSEMOVE));
        SendMessage(hMU, WM_MOUSEACTIVATE, (WPARAM)hMU, MAKELPARAM(HTCLIENT, WM_LBUTTONDOWN));
        SendMessage(hMU, WM_WINDOWPOSCHANGING, 0, (LPARAM)& wp);
        SendMessage(hMU, WM_NCPAINT, 1, 0);
        SendMessage(hMU, WM_WINDOWPOSCHANGED, 0, (LPARAM)& wp);
        SendMessage(hMU, WM_ACTIVATEAPP, 1, 0);
        SendMessage(hMU, WM_NCACTIVATE, 1, 0);
        SendMessage(hMU, WM_ACTIVATE, WA_CLICKACTIVE, 0);
        SendMessage(hMU, WM_SETFOCUS, 0, 0);
        SendMessage(hMU, WM_SETCURSOR, (WPARAM)hMU, MAKELPARAM(HTCLIENT, WM_LBUTTONDOWN));
        PostMessage(hMU, WM_LBUTTONDOWN, MK_LBUTTON, MAKELPARAM(x, y));
        PostMessage(hMU, WM_LBUTTONUP, MK_LBUTTON, MAKELPARAM(x, y));
        SendMessage(hMU, WM_CAPTURECHANGED, 0, 0);
    }

}
The code is probably redundant or even bad, but I don't pay attention to it. I followed the path of exact copying of messages on mouse click. Sorry for this. I will accept any help. Also a Python solution would be nice.

pythonc++winapipywin32
Share
Improve this question
Follow
edited Jul 21, 2020 at 21:45
asked Jul 21, 2020 at 21:41
D3-one 6's user avatar
D3-one 6
5588 bronze badges
Perhaps the game employs anti-cheat measures. –
Sam Varshavchik
CommentedJul 21, 2020 at 21:44
@Sam Varshavchik It is possible. Interestingly, this same game completely accepts my code with simulated keystrokes, but it does not work with simulated mouse clicks. –
D3-one 6
CommentedJul 21, 2020 at 21:52
There is more to simulating input than just sending window messages (and FYI, some of those messages need to be posted with PostMessage(), not sent with SendMessage()). The correct way to simulate input is with SendInput(), but that won't work if the coordinates of the simulated input are covered up by another window. Does the game's window react to UIAutomation instead? –
Remy Lebeau
CommentedJul 21, 2020 at 22:19
Add a comment
1 Answer
Sorted by:

Highest score (default)
0

I recommend you to use SendInput to send the corresponding message to the game window.

You can refer to:How to simulate mouse click in a Directx game.

You can position the focus to the corresponding window by using AttachThreadInput, SetForegroundWindow SetActiveWindow and SetFocus.

Then send the specified message through SendInput.

Share
Improve this answer
Follow
answered Jul 22, 2020 at 2:36
Zeus's user avatar
Zeus
3,91633 gold badges99 silver badges2525 bronze badges
1
AttachThreadInput is like taking two threads and pooling their money into a joint bank account, where both parties need to be present in order to withdraw any money. I warned you: The dangers of attaching input queues. Sharing an input queue takes what used to be asynchronous and makes it synchronous, like focus changes. –
IInspectable
CommentedJul 22, 2020 at 5:41
yes, but I need the window to remain in the same position, that is, so that it does not appear in front –
D3-one 6
CommentedJul 22, 2020 at 15:36
@D3-one6 I think the games uses DirectInput so you can't use SendMessage to send mouse events or keyboard events to it.So you can refer to stackoverflow.com/questions/24735029/directx-game-hook –
Zeus
CommentedJul 23, 2020 at 2:30

====================
Unable to click inside of a game window with pyautogui/win32api/pydirectinput
无法使用 pyautogui/win32api/pydirectinput 在游戏窗口内单击
Asked 3 years, 1 month ago
Modified 7 months ago
Viewed 6k times
2

I can click on the window, but it doesn't move my character, or interact with anything in game. I've tried moving the mouse around, i've tried doing keyboard inputs, full screen, windowed, etc. I've also tried using screenshots with pyautogui, but no luck. The game i'm trying to use it with was initially released in 2000. Non coding wise i've tried running it as admin, running in windows xp sp 2-3 compatibility mode, disabling desktop composition, etc.
我可以单击窗口，但它不会移动我的角色，也不会与游戏中的任何内容交互。我尝试过移动鼠标，我尝试过键盘输入、全屏、窗口等。我还尝试过使用 pyautogui 的屏幕截图，但没有运气。我尝试使用它的游戏最初于 2000 年发布。非编码方面，我尝试以管理员身份运行它，在 Windows XP sp 2-3 兼容模式下运行，禁用桌面组合等。

win32api code: win32api 代码：

import win32api, win32con
import time

def click(x,y):
win32api.SetCursorPos((x,y))
win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN,x,y,0,0)
win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP,x,y,0,0)

# click(573, 841)

# time.sleep(1)

# click(289, 342)

# time.sleep(1)

time.sleep(5)
click(319, 399)
x = win32api.GetCursorPos()
print(x)
error: 错误：

win32api.SetCursorPos((x,y)) pywintypes.error: (0, 'SetCursorPos', 'No error message is available')
pyautogui/pydirect input:
pyautogui/py 直接输入：

import pyautogui
import pydirectinput as p

import time

icon = pyautogui.locateCenterOnScreen('./icon.png', confidence=0.9)
p.click(icon[0], icon[1])
time.sleep(2)
p.press('enter')
this code doesn't throw an error, it completes normally without actually clicking in the game window
此代码不会抛出错误，它正常完成，无需实际单击游戏窗口

pythonpywin32pyautogui
Share
Improve this question
Follow
asked Jun 26, 2022 at 3:36
David Martin's user avatar
David Martin
13311 silver badge99 bronze badges
Add a comment
1 Answer
Sorted by:

Highest score (default)
2

First, make sure you are running your script as admin, sometimes if you don't Windows will prevent mouse movement. Also, try doing this:
首先，确保您以管理员身份运行脚本，有时如果您不这样做，Windows 将阻止鼠标移动。另外，尝试这样做：

def click(x,y):
win32api.SetCursorPos((x, y))
win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
time.sleep(.01)
win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0
You have to give it a little bit of time to click or else Python will do it too quickly and the game won't register it.
你必须给它一点时间来点击，否则 Python 会做得太快，游戏不会注册它。

Share
Improve this answer
Follow
answered Jun 26, 2022 at 3:43
JackElia's user avatar
JackElia
1701010 bronze badges
1
OHH run the SCRIPT as admin. Damnit, that makes sense. I'm over here trying to run the program as admin. That worked! Thank you
哦，以管理员身份运行脚本。该死的，这有道理。我在这里试图以管理员身份运行程序。那有效了！ 谢谢 –
David Martin
CommentedJun 26, 2022 at 14:02

=========================
How to simulate mouse click in a Directx game
Asked 12 years, 7 months ago
Modified 6 years, 11 months ago
Viewed 8k times
1

I have a game written in Directx (not mine it's mmo game). The game window isn't active (not minimized, just it's behind other windows but if is possible it can be minimized also).

I want to simulate mouse click on x, y position. Spy++ doesn't show anything in message when i'm clicking in game.

For now i did just that:

private void start_Click(object sender, EventArgs e)
{
IntPtr ActualWindow = GetActiveWindow();

        ShowWindow(hWnd, ShowWindowCommands.Restore); //show game window
        Thread.Sleep(50);                             //a little slow down
        ClickOnPoint(hWnd, new Point(692, 87));       //click on x,y
        Thread.Sleep(50);                             //again a little slow down
        ShowWindow(hWnd, ShowWindowCommands.Minimize);//minimize game window
        ShowWindow(ActualWindow, ShowWindowCommands.Restore);//restore last active window

//sleep is needed for click, if i will do it too fast game will not detect the click
}

private void ClickOnPoint(IntPtr wndHandle, Point clientPoint)
{
POINT oldPoint;
GetCursorPos(out oldPoint);

        ClientToScreen(wndHandle, ref clientPoint);

        /// set cursor on coords, and press mouse
        SetCursorPos(clientPoint.X, clientPoint.Y);
        mouse_event(0x00000002, 0, 0, 0, UIntPtr.Zero); /// left mouse button down
        Thread.Sleep(18);
        mouse_event(0x00000004, 0, 0, 0, UIntPtr.Zero); /// left mouse button up
        Thread.Sleep(15);

        /// return mouse
        SetCursorPos(oldPoint.X, oldPoint.Y);
    }

It's restore game window click on point and minimize game window.

It's works good, but just when i'm not moving mouse...

I search something else. I want to click mouse without moving it for real. It's even possible do it in game? I don't have any handle for button i want to click because it's a game...

P.S Sorry for my english.

c#c++mousesimulationsimulate
Share
Improve this question
Follow
asked Dec 30, 2012 at 20:16
Kaki's user avatar
Kaki
5533 silver badges99 bronze badges
Nobody plays in this game :D, and i want just click on PLAY button :D, so it's not a bot, i just want to know how i can do it and if it's even possible. This game is Duel of Champions and my skills are too low (for now) to write a AI or neural network :P –
Kaki
CommentedDec 30, 2012 at 20:20
I'd recommend SendMessage instead of trying to control the other window and the mouse. –
Qaz
CommentedDec 30, 2012 at 20:35
A little fail :D I was trying SendMesseage but without effect. Spy++ doesn't show me anything just because i was using a 64bit version of Spy++, 32bit works fine. –
Kaki
CommentedDec 31, 2012 at 14:04
Add a comment
2 Answers
Sorted by:

Highest score (default)
1

Some of my code for simulating a mouse click on a non-active window looks like:

[DllImport("user32.dll", SetLastError = true)]
[return: MarshalAs(UnmanagedType.Bool)]
public static extern bool PostMessage(int hWnd, uint Msg, int wParam, int lParam);

// int MouseX
// int MouseY
// public static readonly uint WM_LBUTTONUP = 0x202;
// public static readonly uint WM_LBUTTONDOWN = 0x201;

int lparam = MouseX & 0xFFFF | (MouseY & 0xFFFF) << 16;
int wparam = 0;
PostMessage(windowHandle, WM_LBUTTONDOWN, wparam, lparam);  
Thread.Sleep(10);  
PostMessage(windowHandle, WM_LBUTTONUP, wparam, lparam);
Share
Improve this answer
Follow
answered Dec 31, 2012 at 18:30
Erik Philips's user avatar
Erik Philips
54.8k1111 gold badges131131 silver badges157157 bronze badges
Look at my first code it's almost the same. In function MakeLParam i have that: MouseX & 0xFFFF | (MouseY & 0xFFFF) << 16; If you can, you can download that game (only 10mb) and try with it. I would be grateful. –
Kaki
CommentedDec 31, 2012 at 18:33
Is the Play button in the game or on a loader? –
Erik Philips
CommentedDec 31, 2012 at 18:39
Also, says the game is not available in my country (US). –
Erik Philips
CommentedDec 31, 2012 at 18:43
Download link: pdc-doc-launcher.ubi.com/launcher/Installer/setup.exe I want try to click on Login button in the game. –
Kaki
CommentedDec 31, 2012 at 18:46
Using process explorer, launcher.exe never loads any directx dlls. The launcher is not using Direct X, so simulating key presses is VERY different. The wHnd you are looking for is most likely an actual MFC control. –
Erik Philips
CommentedDec 31, 2012 at 19:05
Show 7 more comments
1

You can try using Sendiput. here is the Documentation: https://msdn.microsoft.com/en-us/library/windows/desktop/ms646310(v=vs.85).aspx

Here is my example:

Includes:

    #include <stdio.h>
    #include <cstdlib>
    #include <windows.h>
    #include <winuser.h>
    #include <conio.h>

    INPUT input;
    int x = 889;
    int y = 451;

`// Mouse click up
    input.type = INPUT_MOUSE;
    input.mi.mouseData = 0;
    input.mi.dx = x * float(65536 / GetSystemMetrics(SM_CXSCREEN)); //x being coord in pixels
    input.mi.dy = y * float(65536 / GetSystemMetrics(SM_CYSCREEN)); //y being coord in pixels
    input.mi.dwFlags = (MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTDOWN);
    input.mi.time = 0;
    SendInput(1, &input, sizeof(input));`
`   
//Mouse click down
    input.type = INPUT_MOUSE;
    input.mi.mouseData = 0;
    input.mi.dx = x * float(65536 / GetSystemMetrics(SM_CXSCREEN)); //x being coord in pixels
    input.mi.dy = y * float(65536 / GetSystemMetrics(SM_CYSCREEN)); //y being coord in pixels
    input.mi.dwFlags = (MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTUP);
    input.mi.time = 0;
    SendInput(1, &input, sizeof(input));`

And You can do something like this to make sure you are in the focused window:

    HWND hWnd = ::FindWindowEx(0, 0, "YourGame - Use Spy++", 0);

    DWORD dwThreadID = GetWindowThreadProcessId(hWnd, NULL);
    AttachThreadInput(dwThreadID, GetCurrentThreadId(), true);

    SetForegroundWindow(hWnd);
    SetActiveWindow(hWnd);
    SetFocus(hWnd);

    AttachThreadInput(GetWindowThreadProcessId(GetForegroundWindow(), NULL),GetCurrentThreadId(), TRUE);

Share
Improve this answer
Follow
answered Aug 23, 2018 at 21:12
Tauan Binato Flores's user avatar
Tauan Binato Flores
1122 bronze badges
Add a comment
