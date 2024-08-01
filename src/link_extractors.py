from asyncio import sleep

from loguru import logger
from playwright.async_api import (
    Frame,
    Page,
    Route,
)
from playwright.async_api import (
    Request as PlaywrightRequest,
)


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
        except Exception as e:
            logger.error(f"click_elements: 点击失败{e}")
            continue
        finally:
            click_count += 1
    logger.info(f"click_elements: {success_click_count} 个元素点击成功")
    return target_count, success_click_count


def is_top_frame_navigation_request(page: "Page", req: "PlaywrightRequest"):
    """
    判断是否是主页面

    :param page: playwright.Page对象
    :param req: playwright.Request对象
    :return:
    """
    return req.is_navigation_request() and req.frame == page.main_frame


def create_intercept_request_handler(page: "Page", links: list[str]):
    """
    拦截主页面的请求，将拦截到的链接添加到links中

    :param page: playwright.Page对象
    :param links: 链接列表
    :return:
    """

    async def on_intercept_request(route: Route, request: PlaywrightRequest):
        if not is_top_frame_navigation_request(page, request):
            return await route.continue_()
        links.append(request.url)
        return await route.abort("aborted")

    return on_intercept_request


def create_target_created_handler(links):
    """
    新页面事件处理器

    :param links: 链接列表
    :return:
    """

    async def on_target_created(popup: "Page"):
        """
        当新页面被打开时被触发，将链接添加到links中

        :param popup:  playwright.Page对象
        :return:
        """
        url = popup.url
        links.append(url)
        try:
            await popup.close()
        except Exception as e:
            logger.error(f"关闭页面失败: {e}")

    return on_target_created


def create_frame_navigate_handler(page, links: list[str]):
    """
    新frame事件处理器，只有主iframe的链接才会被添加到links中

    :param page: playwright.Page对象
    :param links: 链接列表
    :return:
    """

    def on_frame_navigated(frame: "Frame"):
        if frame != page.main_frame:
            return
        links.append(frame.url)

    return on_frame_navigated


def create_download_handler(links: list[str]):
    """
    下载事件处理器

    :param links: 链接列表
    :return:
    """

    async def on_download(download):
        if not download.url:
            return
        links.append(download.url)
        await download.cancel()

    return on_download


async def wait_for_page_idle(page: Page, wait_time: int = 2, max_wait_time: int = 10):
    """
    等待页面加载
    todo 实际应是等待on_popup事件被触发，links被更新，而不是直接sleep写死时间

    :param page: playwright.Page对象
    :param wait_time: 等待时间
    :param max_wait_time: 最大等待时间
    :return:
    """
    return await sleep(wait_time)


async def click_element_and_intercept_navigation_request(
        page: "Page",
        xpath: str,
        wait_time,
        max_wait_time: int,
        force_wait: bool = True,
        click_sleep: int | float = 0,
        click_timeout: int | float = 2,
):
    """
    点击元素并且拦截点击之后触发的请求，将请求的链接添加到links中
    请求包括：主页面的请求、新页面的请求、新frame的请求

    :param page: playwright.Page对象
    :param xpath: xpath
    :param wait_time: 等待时间
    :param max_wait_time: 最大等待时间
    :param force_wait: 是否强制等待元素加载完成
    :param click_sleep: 点击元素之后等待时间
    :param click_timeout: 点击元素的超时时间
    :return: links, target_count, click_count
    """
    links = []
    on_intercept_request = create_intercept_request_handler(page, links)
    on_target_created = create_target_created_handler(links)
    on_frame_navigated = create_frame_navigate_handler(page, links)
    on_download = create_download_handler(links)
    await page.route("**", on_intercept_request)
    page.on("framenavigated", on_frame_navigated)
    page.on("popup", on_target_created)
    page.on("download", on_download)
    target_count, click_count = await click_elements(
        page, xpath, force_wait, click_sleep, click_timeout
    )
    await wait_for_page_idle(page, wait_time, max_wait_time)
    page.remove_listener("framenavigated", on_frame_navigated)
    page.remove_listener("popup", on_target_created)
    page.remove_listener("download", on_download)
    await page.unroute("**", on_intercept_request)
    return links, target_count, click_count


async def extract_links_by_click_elements(
        page: "Page",
        xpath: str,
        force_wait: bool = True,
        click_sleep: int | float = 0,
        click_timeout: int | float = 2,
) -> tuple[list[str], int, int]:
    """
    通过点击元素获取链接，默认会对链接去重，并且会去除page url

    :param page: playwright.Page对象
    :param xpath: xpath
    :param force_wait: 是否强制等待元素
    :param click_sleep: 点击后的等待时间
    :param click_timeout: 点击操作的超时时间
    :return:
    """
    wait_time, max_wait_time = 2, 10
    all_links = []
    target_count = 0
    click_count = 0

    # 如果是frame则直接返回，frame不支持route
    if isinstance(page, Frame):
        return all_links, target_count, click_count

    (
        all_links,
        target_count,
        click_count,
    ) = await click_element_and_intercept_navigation_request(
        page, xpath, wait_time, max_wait_time, force_wait, click_sleep, click_timeout
    )
    # 去除page url, 防止死循环, 有些页面会自动跳转到自己
    all_links = [link for link in all_links if link != page.url]
    return all_links, target_count, click_count
