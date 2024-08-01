from asyncio import Event, sleep
from typing import Any, Dict

from loguru import logger
from playwright.async_api import (
    Download,
    Frame,
    Page,
    Route,
)
from playwright.async_api import (
    Request as PlaywrightRequest,
)

invalid_urls = [":", "", "about:black", None]


async def click_elements(
        page: "Page",
        xpath: str,
        force_wait: bool = True,
        click_sleep: int | float = 0,
        click_timeout: int | float = 2,
):
    """
    点击元素， 同时记录点击的元素个数和点击成功的元素个数
    默认会将元素的z_index置为最大值，防止点击不到

    :param page: playwright.Page对象
    :param xpath: xpath
    :param force_wait: 是否强制等待元素加载完成
    :param click_sleep: 点击后等待时间
    :param click_timeout: 点击的超时时间
    :return:
    """
    if force_wait:
        await page.wait_for_selector(f"xpath={xpath}")
        elements = await page.locator(f"xpath={xpath}").element_handles()
    else:
        elements = await page.query_selector_all(f"xpath={xpath}")
    target_count = len(elements)
    logger.info(f"click_elements: {target_count} 个元素等待被点击")
    click_count = 0
    success_click_count = 0
    z_index = 2147400000
    click_timeout = click_timeout * 1000
    elements_text = []
    for element in elements:
        try:
            # 将元素的z_index置为最大值，防止点击不到
            await page.evaluate(
                """
            ([element, zIndex]) => {
            element.style.zIndex = String(zIndex);
    }""",
                [element, z_index + click_count],
            )
            if await element.is_visible() and await element.is_enabled():
                text = await element.inner_text()
                logger.info(f"click_elements: 点击第{click_count + 1}个元素 {text}")
                await element.click(timeout=click_timeout)
                success_click_count += 1
                await sleep(click_sleep)

                elements_text.append(text)
        except Exception as e:
            logger.error(f"click_elements: 点击失败{e}")
            continue
        finally:
            click_count += 1
    logger.info(f"click_elements: {success_click_count} 个元素点击成功")
    return elements_text


def is_top_frame_navigation_request(page: "Page", req: "PlaywrightRequest"):
    """
    判断是否是主页面

    :param page: playwright.Page对象
    :param req: playwright.Request对象
    :return:
    """
    return req.is_navigation_request() and req.frame == page.main_frame


def create_intercept_request_handler(
        page: "Page",
        files: list[Dict[str, Any]],
):
    """
    拦截主页面的请求，将拦截到的链接添加到files中

    :param page: playwright.Page对象
    :param files: 附件列表
    :param event
    :return:
    """

    async def on_intercept_request(route: Route, request: PlaywrightRequest):
        if not is_top_frame_navigation_request(page, request):
            return await route.continue_()
        if request.url not in invalid_urls and request.url != page.url:
            files.append({"url": request.url})
        return await route.abort("aborted")

    return on_intercept_request


def create_target_created_handler(files: list[Dict[str, Any]]):
    """
    新页面事件处理器

    :param files: 附件列表
    :return:
    """

    async def on_target_created(popup: "Page"):
        """
        当新页面被打开时被触发，将链接添加到links中

        :param popup:  playwright.Page对象
        :return:
        """
        if popup.url not in invalid_urls:
            files.append({"url": popup.url})
        try:
            await popup.close()
        except Exception as e:
            logger.error(f"关闭页面失败: {e}")

    return on_target_created


def create_frame_navigate_handler(page, files: list[Dict[str, Any]]):
    """
    新frame事件处理器，只有主iframe的链接才会被添加到links中

    :param page: playwright.Page对象
    :param files: 附件列表
    :return:
    """

    def on_frame_navigated(frame: "Frame"):
        if frame != page.main_frame or frame.url in invalid_urls:
            return
        files.append({"url": frame.url})

    return on_frame_navigated


def create_dialog_handler():
    """
    弹窗事件处理器
    :return:
    """

    async def on_dialog(dialog):
        logger.info(f"Dialog message: {dialog.message}")
        await dialog.dismiss()

    return on_dialog


def create_download_handler(page, files: list[Dict[str, Any]], events: list[Event]):
    """
    下载事件处理器

    :param page: playwright.Page对象
    :param files: 附件
    :param events: 事件列表
    :return:
    """

    async def on_download(download: Download):
        download_event = Event()
        events.append(download_event)
        try:
            file_path = await download.path()
            with open(file_path, "rb") as f:
                file = {
                    "url": download.url,
                    "filename": download.suggested_filename,
                    "content": f.read(),
                }
                files.append(file)
        except Exception as e:
            logger.error(f"download {page.url} file {download.url} error {e}")
        finally:
            download_event.set()

    return on_download


async def click_element_and_intercept_navigation_request(
        page: "Page",
        xpath: str,
        wait_time,
        force_wait: bool = True,
        click_sleep: int | float = 0,
        click_timeout: int | float = 2,
):
    """
    点击元素并且拦截点击之后触发的请求，将请求的链接添加到file中
    请求包括：主页面的请求、新页面的请求、新frame、下载的请求

    :param page: playwright.Page对象
    :param xpath: xpath
    :param wait_time: 等待时间
    :param force_wait: 是否强制等待元素加载完成
    :param click_sleep: 点击元素之后等待时间
    :param click_timeout: 点击元素的超时时间
    :return: links, target_count, click_count
    """
    files = []
    events = []
    on_intercept_request = create_intercept_request_handler(page, files)
    on_target_created = create_target_created_handler(files)
    on_frame_navigated = create_frame_navigate_handler(page, files)
    on_download = create_download_handler(page, files, events)
    on_dialog = create_dialog_handler()
    await page.route("**", on_intercept_request)
    page.on("framenavigated", on_frame_navigated)
    page.on("dialog", on_dialog)
    page.on("popup", on_target_created)
    page.on("download", on_download)
    elements_text = await click_elements(
        page, xpath, force_wait, click_sleep, click_timeout
    )
    await sleep(wait_time)
    page.remove_listener("framenavigated", on_frame_navigated)
    page.remove_listener("popup", on_target_created)
    page.remove_listener("download", on_download)
    page.remove_listener("dialog", on_dialog)
    await page.unroute("**", on_intercept_request)

    # 等待有事件被注册，则需要等待事件完成（目前主要是等待下载结束）
    for event in events:
        await event.wait()
    return files, elements_text


async def extract_files_by_click_elements(
        page: "Page",
        xpath: str,
        force_wait: bool = False,
        click_sleep: int | float = 1.5,
        click_timeout: int | float = 2,
) -> list[Dict[str, Any]]:
    """
    通过点击元素获取附件

    :param page: playwright.Page对象
    :param xpath: xpath
    :param force_wait: 是否强制等待元素
    :param click_sleep: 点击后的等待时间
    :param click_timeout: 点击操作的超时时间
    :return:
    """
    wait_time = 2
    files = []
    # 如果是frame则直接返回，frame不支持route
    if isinstance(page, Frame):
        return files
    (
        files,
        elements_text,
    ) = await click_element_and_intercept_navigation_request(
        page, xpath, wait_time, force_wait, click_sleep, click_timeout
    )
    if len(files) == len(elements_text):
        for index, file in enumerate(files):
            if not file.get("filename"):
                file["filename"] = elements_text[index]

    return files
